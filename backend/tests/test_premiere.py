"""Editor bridge regressions: local authentication, original files, and durable results."""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.services import editor_bridge, job_runner, media_cleanup
from app.services.job_store import Job, create_job, get_job, list_jobs, update_job


@pytest.fixture
def editor(client):
    token = editor_bridge.get_pairing_token()
    return TestClient(app, client=('127.0.0.1', 52000), headers={'Authorization': f'Bearer {token}'})


@pytest.fixture
def source(tmp_path, monkeypatch):
    path = tmp_path / 'Original film.mp4'
    path.write_bytes(b'original-media-never-to-be-copied-or-deleted')
    monkeypatch.setattr(editor_bridge, 'probe_media', AsyncMock(return_value={
        'duration_seconds': 8, 'frame_rate': 25, 'width': 1920, 'height': 1080, 'has_audio': True,
    }))
    monkeypatch.setattr(editor_bridge, 'create_poster', AsyncMock())
    return {'project_id': 'premiere-project-1', 'item_id': 'source-1',
            'media_path': str(path), 'name': 'Source film', 'source_fps': 25}


def register(editor, source):
    response = editor.post('/api/editor/premiere/register', json=source)
    assert response.status_code == 200, response.text
    return response.json()


def test_bridge_requires_both_pairing_and_loopback(client):
    route = '/api/editor/premiere/health'
    assert client.get(route).status_code == 403
    token = editor_bridge.get_pairing_token()
    outside = TestClient(app, client=('203.0.113.10', 80), headers={'Authorization': f'Bearer {token}'})
    assert outside.get(route).status_code == 403
    local = TestClient(app, client=('127.0.0.1', 80))
    assert local.get(route).status_code == 401
    assert local.get(route, headers={'Authorization': 'Bearer wrong'}).status_code == 401
    assert local.get(route, headers={'Authorization': f'Bearer {token}'}).status_code == 200


def test_pair_token_persists_and_rotation_revokes_previous_token(editor):
    old = editor_bridge.get_pairing_token()
    assert old == editor_bridge.get_pairing_token()
    fresh = editor_bridge.get_pairing_token(rotate=True)
    assert old != fresh
    assert editor.get('/api/editor/premiere/health').status_code == 401
    reply = editor.get('/api/editor/premiere/health', headers={'Authorization': f'Bearer {fresh}'})
    assert reply.status_code == 200
    assert fresh not in reply.text


def test_register_references_original_without_copy_or_analysis(editor, source, monkeypatch):
    monkeypatch.setattr(job_runner, 'start_job', lambda *args: pytest.fail('Import must not call Gemini'))
    original = Path(source['media_path'])
    before = original.read_bytes(), original.stat().st_mtime_ns
    asset = register(editor, source)
    job = get_job(asset['job_id'])
    assert job.media_origin == 'external'
    assert Path(job.local_path) == original
    assert (original.read_bytes(), original.stat().st_mtime_ns) == before
    assert not list(Path(settings.upload_dir).glob('*.mp4'))
    assert asset['editor_sources'][0]['source_fps'] == 25
    assert asset['status'] == 'ready'
    assert job.file_id == ''


def test_register_deduplicates_file_across_items_and_projects(editor, source):
    first = register(editor, source)
    assert register(editor, source)['job_id'] == first['job_id']
    second = register(editor, {**source, 'item_id': 'source-2', 'project_id': 'project-2'})
    assert second['job_id'] == first['job_id']
    assert len(list_jobs()) == 1
    for project in (source['project_id'], 'project-2'):
        view = editor.get('/api/editor/premiere/assets', params={'project_id': project}).json()
        assert [entry['job_id'] for entry in view['assets']] == [first['job_id']]
    assert editor.get('/api/editor/premiere/assets', params={'project_id': 'unrelated'}).json()['assets'] == []


def test_load_desktop_analysis_without_editor_binding(editor, source, monkeypatch):
    fingerprint, original, size = editor_bridge.source_fingerprint(source['media_path'])
    job = create_job(Job(job_id='desktop-import', file_id='', file_uri='', filename=original.name,
        mime_type='video/mp4', size_bytes=size, local_path=str(original), media_origin='external',
        source_fingerprint=fingerprint, technical={'duration_seconds': 8, 'frame_rate': 25},
        status='complete', metadata={'title': 'Logged take', 'tags': ['keep']},
        flash_result={'scenes': []}, transcript=[{'start_seconds': 1, 'end_seconds': 2, 'text': 'Saved dialogue'}]))
    monkeypatch.setattr(editor_bridge, 'probe_media', AsyncMock(side_effect=AssertionError('Do not reprobe saved media')))
    loaded = register(editor, source)
    assert loaded['job_id'] == job.job_id
    assert loaded['transcript'] == job.transcript
    assert get_job(job.job_id).metadata == job.metadata
    assert len(list_jobs()) == 1
    assert editor_bridge.editor_sources(job.job_id)[0]['item_id'] == source['item_id']


def test_relinked_item_gets_new_asset_and_preserves_old_analysis(editor, source, tmp_path):
    first = register(editor, source)
    update_job(first['job_id'], transcript=[{'start_seconds': 0, 'end_seconds': 2, 'text': 'Old dialogue'}])
    replacement = tmp_path / 'New film.mp4'
    replacement.write_bytes(b'different-source')
    new = register(editor, {**source, 'media_path': str(replacement)})
    assert new['job_id'] != first['job_id']
    assert not new['transcript']
    assert get_job(first['job_id']).transcript[0]['text'] == 'Old dialogue'
    assert not editor_bridge.editor_sources(first['job_id'])
    assets = editor.get('/api/editor/premiere/assets', params={'project_id': source['project_id']}).json()
    assert [a['job_id'] for a in assets['assets']] == [new['job_id']]


@pytest.mark.parametrize('overrides', [
    {'media_path': 'relative.mp4'}, {'media_path': '/does/not/exist.mp4'},
    {'media_path': 'https://example.com/film.mp4'}, {'project_id': ' '}, {'item_id': ''},
    {'source_fps': 0}, {'source_fps': -1}, {'unknown': 'bad'},
])
def test_invalid_registration_does_not_create_asset(editor, source, overrides):
    assert editor.post('/api/editor/premiere/register', json={**source, **overrides}).status_code == 422
    assert not list_jobs()


def test_interpreted_footage_requires_original_frame_rate(editor, source):
    response = editor.post('/api/editor/premiere/register', json={**source, 'source_fps': 24})
    assert response.status_code == 422
    assert 'frame rate' in response.json()['detail']
    assert not list_jobs()


def test_changed_source_is_blocked_before_paid_analysis(editor, source, monkeypatch):
    asset = register(editor, source)
    settings.google_api_key = 'test-key'
    monkeypatch.setattr(job_runner, 'start_job', lambda *args: pytest.fail('No paid call for changed source'))
    Path(source['media_path']).write_bytes(b'replaced')
    response = editor.post(f"/api/editor/premiere/assets/{asset['job_id']}/analyze", json={'mode': 'flash_pro'})
    assert response.status_code == 409
    assert get_job(asset['job_id']).status == 'ready'
    assert editor.get(f"/api/editor/premiere/assets/{asset['job_id']}/shots").status_code == 409
    assert editor.get(f"/api/editor/premiere/assets/{asset['job_id']}").status_code == 409
    assert editor.get(f"/api/editor/premiere/assets/{asset['job_id']}/subtitles").status_code == 409
    new = register(editor, source)
    assert new['job_id'] != asset['job_id']


def test_deleted_external_asset_preserves_original_even_inside_cache(editor, source):
    asset = register(editor, source)
    response = editor.delete(f"/api/files/{asset['job_id']}")
    assert response.status_code == 200
    assert Path(source['media_path']).read_bytes() == b'original-media-never-to-be-copied-or-deleted'
    assert editor_bridge.editor_sources(asset['job_id']) == []
    assert editor.get('/api/editor/premiere/assets', params={'project_id': source['project_id']}).json()['assets'] == []
    root = Path(settings.upload_dir)
    root.mkdir(parents=True, exist_ok=True)
    unusual = root / 'external-id.mov'
    unusual.write_bytes(b'original')
    inside = root / 'nested-source'
    inside.mkdir()
    nested = inside / 'original.mov'
    nested.write_bytes(b'original')
    for ident, path in [('external-id', unusual), ('nested-source', nested)]:
        job = Job(job_id=ident, file_id='', file_uri='', mime_type='video/quicktime', filename=path.name,
                  size_bytes=8, local_path=str(path), media_origin='external')
        assert media_cleanup.cleanup(job)
        assert path.read_bytes() == b'original'


def test_source_changed_while_queued_never_reaches_provider(editor, source, monkeypatch):
    from app.models.library import AnalyzeRequest
    asset = register(editor, source)
    upload = AsyncMock()
    monkeypatch.setattr(job_runner, 'ensure_remote_file', upload)

    async def scenario():
        gate = asyncio.Semaphore(1)
        await gate.acquire()
        previous_gate = job_runner._gate
        job_runner._gate = gate
        try:
            task = asyncio.create_task(job_runner._run(asset['job_id'], AnalyzeRequest(), None))
            await asyncio.sleep(0)
            assert not task.done()
            Path(source['media_path']).write_bytes(b'replaced-after-queueing')
            gate.release()
            await task
        finally:
            job_runner._gate = previous_gate

    asyncio.run(scenario())
    upload.assert_not_awaited()
    job = get_job(asset['job_id'])
    assert job.status == 'error'
    assert 'source is offline or changed' in job.error


def test_progressive_shots_and_actual_dialogue_are_available(editor, source):
    asset = register(editor, source)
    update_job(asset['job_id'], status='analyzing', analysis_progress={'completed_sections': 1, 'total_sections': 2},
               flash_result={'scenes': [{'scene_number': 1, 'shots': [{
                   'shot_number': 1, 'start_time': '00:00.000', 'end_time': '00:03.000',
                   'visual_description': 'Blue water', 'tags': ['blue'],
               }]}]}, transcript=[{'start_seconds': 1, 'end_seconds': 2, 'text': 'Hello sailor'}])
    detail = editor.get(f"/api/editor/premiere/assets/{asset['job_id']}").json()
    assert detail['analysis_progress']['completed_sections'] == 1
    shots = editor.get(f"/api/editor/premiere/assets/{asset['job_id']}/shots", params={'q': 'sailor'}).json()
    assert shots['total'] == 1 and 'Transcript' in shots['shots'][0]['match_sources']
    srt = editor.get(f"/api/editor/premiere/assets/{asset['job_id']}/subtitles")
    assert srt.status_code == 200 and 'Hello sailor' in srt.text
    assert '00:00:01,000 --> 00:00:02,000' in srt.text


def test_analysis_is_explicit_uses_selected_mode_and_existing_server_key(editor, source, monkeypatch):
    asset = register(editor, source)
    settings.google_api_key = 'test-key'
    calls = []
    from app.routers import library
    monkeypatch.setattr(library, 'start_job', lambda ident, request, key: calls.append((ident, request.mode, key)))
    response = editor.post(f"/api/editor/premiere/assets/{asset['job_id']}/analyze", json={'mode': 'flash_pro'})
    assert response.status_code == 202
    assert calls == [(asset['job_id'], 'flash_pro', None)]
    assert editor.post(f"/api/editor/premiere/assets/{asset['job_id']}/analyze", json={'mode': 'flash_only'}).status_code == 409


def test_desktop_key_settings_are_private_and_not_returned(editor, monkeypatch):
    assert editor.post('/api/editor/premiere/settings', json={'google_api_key': 'test-secret'}).status_code == 409
    monkeypatch.setenv('VIDEO_ANALYZER_DESKTOP', '1')
    response = editor.post('/api/editor/premiere/settings', json={'google_api_key': 'test-secret'})
    assert response.status_code == 200 and response.json()['google_configured']
    assert 'test-secret' not in response.text
    saved = Path(settings.database_path).parent / 'desktop-settings.json'
    assert json.loads(saved.read_text()) == {'google_api_key': 'test-secret'}
    assert editor.post('/api/editor/premiere/settings', json={'google_api_key': ''}).status_code == 200
    assert not settings.google_api_key


def test_concurrent_registration_creates_one_record(editor, source):
    body = editor_bridge.PremiereSource(**source)

    async def scenario():
        return await asyncio.gather(editor_bridge.register_source(body), editor_bridge.register_source(body))

    first, second = asyncio.run(scenario())
    assert first['job_id'] == second['job_id']
    assert len(list_jobs()) == 1
