"""Resolve's local bridge shares analysis, keeps host identities separate, and preserves media."""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.services import editor_bridge, job_runner
from app.services.job_store import get_job, list_jobs, update_job

PREFIX = '/api/editor/resolve'


@pytest.fixture
def resolve_editor(client):
    token = editor_bridge.get_pairing_token()
    return TestClient(app, client=('127.0.0.1', 52000), headers={'Authorization': f'Bearer {token}'})


@pytest.fixture
def resolve_source(tmp_path, monkeypatch):
    path = tmp_path / 'Original Resolve film.mp4'
    path.write_bytes(b'original-resolve-media-never-to-be-copied-or-deleted')
    monkeypatch.setattr(editor_bridge, 'probe_media', AsyncMock(return_value={
        'duration_seconds': 8, 'frame_rate': 25, 'width': 1920, 'height': 1080, 'has_audio': True,
    }))
    monkeypatch.setattr(editor_bridge, 'create_poster', AsyncMock())
    return {'project_id': 'shared-project-id', 'item_id': 'shared-item-id',
            'media_path': str(path), 'name': 'Resolve source', 'source_fps': 25}


def register(editor, body, host='resolve'):
    response = editor.post(f'/api/editor/{host}/register', json=body)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize('method,route,body', [
    ('get', '/health', None), ('get', '/assets?project_id=project', None),
    ('post', '/register', {}), ('post', '/settings', {'google_api_key': ''}),
    ('get', '/assets/unknown', None), ('get', '/assets/unknown/shots', None),
    ('get', '/assets/unknown/subtitles', None), ('post', '/assets/unknown/analyze', {}),
    ('post', '/assets/unknown/shot-package', {'shots': [{'shot_number': 1, 'start': 0, 'end': 1}]}),
])
def test_every_resolve_route_requires_pairing_and_loopback(client, method, route, body):
    token = editor_bridge.get_pairing_token()
    for address, authorization, expected in [
        ('203.0.113.10', f'Bearer {token}', 403), ('127.0.0.1', None, 401),
        ('127.0.0.1', 'Bearer wrong', 401),
    ]:
        headers = {'Authorization': authorization} if authorization else {}
        request = TestClient(app, client=(address, 52000), headers=headers)
        kwargs = {'json': body} if body is not None else {}
        response = request.request(method, PREFIX + route, **kwargs)
        assert response.status_code == expected
        assert token not in response.text


def test_resolve_and_premiere_use_the_same_revocable_pairing(resolve_editor):
    response = resolve_editor.get(PREFIX + '/health')
    assert response.status_code == 200
    assert response.json()['host'] == 'resolve'
    assert response.json()['protocol_version'] == 1
    assert resolve_editor.get('/api/editor/premiere/health').json()['host'] == 'premiere'
    fresh = editor_bridge.get_pairing_token(rotate=True)
    assert resolve_editor.get(PREFIX + '/health').status_code == 401
    response = resolve_editor.get(PREFIX + '/health', headers={'Authorization': f'Bearer {fresh}'})
    assert response.status_code == 200 and fresh not in response.text
    ipv6 = TestClient(app, client=('::1', 52000), headers={'Authorization': f'Bearer {fresh}'})
    assert ipv6.get(PREFIX + '/health').status_code == 200


def test_import_references_original_and_never_starts_analysis(resolve_editor, resolve_source, monkeypatch):
    monkeypatch.setattr(job_runner, 'start_job', lambda *args: pytest.fail('Import cannot call Gemini'))
    original = Path(resolve_source['media_path'])
    before = original.read_bytes(), original.stat().st_mtime_ns
    asset = register(resolve_editor, resolve_source)
    job = get_job(asset['job_id'])
    assert job.media_origin == 'external' and Path(job.local_path) == original
    assert (original.read_bytes(), original.stat().st_mtime_ns) == before
    assert not list(Path(settings.upload_dir).rglob('*.mp4'))
    assert asset['status'] == 'ready' and not job.file_id
    assert asset['editor_sources'] == [{
        'host': 'resolve', 'project_id': resolve_source['project_id'], 'item_id': resolve_source['item_id'],
        'media_path': str(original), 'name': resolve_source['name'], 'source_fps': 25,
    }]


def test_identical_project_and_item_ids_do_not_mix_editors(resolve_editor, resolve_source, tmp_path):
    premiere = register(resolve_editor, {**resolve_source, 'name': 'Premiere film'}, host='premiere')
    other = tmp_path / 'Different Resolve source.mp4'
    other.write_bytes(b'different-resolve-source')
    resolve = register(resolve_editor, {**resolve_source, 'media_path': str(other)})
    assert premiere['job_id'] != resolve['job_id']
    for host, expected in [('premiere', premiere), ('resolve', resolve)]:
        result = resolve_editor.get(f'/api/editor/{host}/assets',
                                    params={'project_id': resolve_source['project_id']}).json()
        assert result['total'] == 1
        assert [a['job_id'] for a in result['assets']] == [expected['job_id']]
        assert {s['host'] for s in result['assets'][0]['editor_sources']} == {host}
    assert resolve_editor.get(PREFIX + '/assets', params={'project_id': 'unrelated'}).json()['assets'] == []


def test_same_original_reuses_analysis_across_editors_without_leaking_identities(resolve_editor, resolve_source):
    premiere = register(resolve_editor, {**resolve_source, 'project_id': 'premiere-only', 'name': 'Premiere name'},
                        host='premiere')
    update_job(premiere['job_id'], transcript=[{'start_seconds': 1, 'end_seconds': 2, 'text': 'Shared dialogue'}])
    resolve = register(resolve_editor, resolve_source)
    again = register(resolve_editor, {**resolve_source, 'item_id': 'other-item', 'project_id': 'other-project'})
    assert premiere['job_id'] == resolve['job_id'] == again['job_id']
    assert len(list_jobs()) == 1
    assert resolve['transcript'][0]['text'] == 'Shared dialogue'
    assert all(s['host'] == 'resolve' for s in resolve['editor_sources'])
    assert all(s['project_id'] != 'premiere-only' for s in resolve['editor_sources'])
    assert {s['host'] for s in editor_bridge.editor_sources(resolve['job_id'])} == {'resolve', 'premiere'}


@pytest.mark.parametrize('suffix,method', [('', 'get'), ('/shots', 'get'), ('/subtitles', 'get'), ('/analyze', 'post')])
def test_resolve_job_routes_require_a_resolve_registration(resolve_editor, resolve_source, suffix, method):
    asset = register(resolve_editor, resolve_source, host='premiere')
    kwargs = {'json': {'mode': 'flash_pro'}} if method == 'post' else {}
    response = resolve_editor.request(method, PREFIX + f"/assets/{asset['job_id']}" + suffix, **kwargs)
    assert response.status_code == 404
    assert 'registered with Resolve' in response.json()['detail']
    assert get_job(asset['job_id']).status == 'ready'


def test_relink_only_moves_the_resolve_binding_and_preserves_old_results(resolve_editor, resolve_source, tmp_path):
    old = register(resolve_editor, resolve_source)
    register(resolve_editor, resolve_source, host='premiere')
    update_job(old['job_id'], transcript=[{'start_seconds': 1, 'end_seconds': 2, 'text': 'Old dialogue'}])
    other = tmp_path / 'Relinked source.mp4'
    other.write_bytes(b'relinked-original-media')
    new = register(resolve_editor, {**resolve_source, 'media_path': str(other)})
    assert old['job_id'] != new['job_id'] and not new['transcript']
    assert get_job(old['job_id']).transcript[0]['text'] == 'Old dialogue'
    assert [s['host'] for s in editor_bridge.editor_sources(old['job_id'])] == ['premiere']
    assert [s['host'] for s in editor_bridge.editor_sources(new['job_id'])] == ['resolve']
    assert resolve_editor.get(PREFIX + f"/assets/{old['job_id']}").status_code == 404
    premiere_assets = editor_bridge.project_assets(resolve_source['project_id'])
    assert [a['job_id'] for a in premiere_assets] == [old['job_id']]


def test_changed_original_blocks_every_read_and_paid_analysis(resolve_editor, resolve_source, monkeypatch):
    asset = register(resolve_editor, resolve_source)
    settings.google_api_key = 'test-key'
    from app.routers import library
    monkeypatch.setattr(library, 'start_job', lambda *args: pytest.fail('Changed source cannot call Gemini'))
    Path(resolve_source['media_path']).write_bytes(b'changed-original')
    path = PREFIX + f"/assets/{asset['job_id']}"
    for suffix in ('', '/shots', '/subtitles'):
        assert resolve_editor.get(path + suffix).status_code == 409
    assert resolve_editor.post(path + '/analyze', json={'mode': 'flash_pro'}).status_code == 409
    assert resolve_editor.post(path + '/shot-package', json={'shots': [{'shot_number': 1, 'start': 0, 'end': 1}]}).status_code == 409
    assert get_job(asset['job_id']).status == 'ready'
    assert register(resolve_editor, resolve_source)['job_id'] != asset['job_id']


def seed_shots(editor, source):
    asset = register(editor, source)
    update_job(asset['job_id'], analysis_config={'started_at': 'session-1'},
               flash_result={'scenes': [{'scene_number': 1, 'scene_title': 'Desert section', 'shots': [
                   {'shot_number': 1, 'start_time': '00:00.000', 'end_time': '00:02.000',
                    'visual_description': 'A red circle', 'tags': ['circle'], 'dominant_colors': ['red']},
                   {'shot_number': 2, 'start_time': '00:02.000', 'end_time': '00:04.000',
                    'visual_description': 'A person', 'tags': ['person']},
               ]}]}, deep_results=[{'scene_number': 1, 'composition': 'Centered shapes', 'api_key': 'private'}],
               transcript=[{'start_seconds': 1, 'end_seconds': 2, 'text': 'Hello there'}],
               shot_annotations={'1': {'tags': ['approved'], 'notes': 'Editor note'}})
    return asset, {'analysis_session': 'session-1', 'shots': [
        {'shot_number': 1, 'start': 0, 'end': 2}, {'shot_number': 2, 'start': 2, 'end': 4}]}


def test_shot_package_contains_public_shot_section_dialogue_and_review_metadata(resolve_editor, resolve_source, monkeypatch):
    monkeypatch.setattr(job_runner, 'start_job', lambda *args: pytest.fail('Shot export cannot call Gemini'))
    asset, body = seed_shots(resolve_editor, resolve_source)
    original = Path(resolve_source['media_path'])
    before = original.read_bytes(), original.stat().st_mtime_ns
    response = resolve_editor.post(PREFIX + f"/assets/{asset['job_id']}/shot-package", json={**body, 'shots': list(reversed(body['shots']))})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['analysis_session'] == 'session-1'
    assert [shot['shot_number'] for shot in data['shots']] == [1, 2]
    first = data['shots'][0]
    assert first['tags'] == ['circle', 'approved'] and first['notes'] == 'Editor note'
    assert first['section_analysis']['composition'] == 'Centered shapes'
    assert first['transcript_segments'][0]['text'] == 'Hello there'
    assert 'private' not in response.text and 'api_key' not in response.text
    assert 'thumbnail_url' not in first and 'preview_url' not in first
    assert any(field['key'] == 'transcript' and 'Hello there' in field['value'] for field in first['editor_metadata']['fields'])
    assert (original.read_bytes(), original.stat().st_mtime_ns) == before


def test_premiere_metadata_snapshot_keeps_complete_analysis_and_rejects_stale_session(resolve_editor, resolve_source):
    asset, _ = seed_shots(resolve_editor, resolve_source)
    path = f"/api/editor/premiere/assets/{asset['job_id']}/metadata"
    response = resolve_editor.get(path, params={'analysis_session': 'session-1'})
    assert response.status_code == 200, response.text
    record = response.json()['record']
    assert record['analysis']['deep_results'][0]['composition'] == 'Centered shapes'
    assert record['transcript'][0]['text'] == 'Hello there'
    assert record['shots'][0]['notes'] == 'Editor note'
    assert 'api_key' not in response.text and 'private' not in response.text
    assert resolve_editor.get(path, params={'analysis_session': 'old'}).status_code == 409
    Path(resolve_source['media_path']).write_bytes(b'changed original')
    assert resolve_editor.get(path, params={'analysis_session': 'session-1'}).status_code == 409


@pytest.mark.parametrize('patch', [
    {'analysis_session': 'old'}, {'shots': [{'shot_number': 1, 'start': 0.1, 'end': 2}]},
    {'shots': [{'shot_number': 1, 'start': 0, 'end': 1.9}]},
    {'shots': [{'shot_number': 99, 'start': 0, 'end': 2}]},
    {'shots': [{'shot_number': 1, 'start': 2, 'end': 1}]},
])
def test_stale_shot_package_is_rejected_atomically(resolve_editor, resolve_source, patch):
    asset, body = seed_shots(resolve_editor, resolve_source)
    response = resolve_editor.post(PREFIX + f"/assets/{asset['job_id']}/shot-package", json={**body, **patch})
    assert response.status_code == 409


@pytest.mark.parametrize('shots', [[], [{'shot_number': True, 'start': 0, 'end': 2}],
    [{'shot_number': 1, 'start': 0, 'end': 2}] * 2,
    [{'shot_number': i + 1, 'start': 0, 'end': 2} for i in range(51)]])
def test_invalid_shot_package_selections_are_rejected(resolve_editor, resolve_source, shots):
    asset, body = seed_shots(resolve_editor, resolve_source)
    assert resolve_editor.post(PREFIX + f"/assets/{asset['job_id']}/shot-package", json={**body, 'shots': shots}).status_code == 422


def test_shot_package_requires_resolve_binding(resolve_editor, resolve_source):
    asset = register(resolve_editor, resolve_source, host='premiere')
    assert resolve_editor.post(PREFIX + f"/assets/{asset['job_id']}/shot-package", json={'shots': [{'shot_number': 1, 'start': 0, 'end': 2}]}).status_code == 404


def test_deleted_library_asset_removes_both_bindings_and_keeps_original(resolve_editor, resolve_source):
    asset = register(resolve_editor, resolve_source)
    register(resolve_editor, resolve_source, host='premiere')
    original = Path(resolve_source['media_path'])
    before = original.read_bytes(), original.stat().st_mtime_ns
    assert resolve_editor.delete(f"/api/files/{asset['job_id']}").status_code == 200
    assert (original.read_bytes(), original.stat().st_mtime_ns) == before
    assert not editor_bridge.editor_sources(asset['job_id'])
    assert not editor_bridge.project_assets(resolve_source['project_id'], host='resolve')
    assert not editor_bridge.project_assets(resolve_source['project_id'], host='premiere')


@pytest.mark.parametrize('overrides', [
    {'media_path': 'relative.mp4'}, {'media_path': '/not/a/real/file.mp4'}, {'project_id': ' '},
    {'item_id': ''}, {'source_fps': 0}, {'source_fps': -1}, {'host': 'premiere'},
])
def test_invalid_sources_cannot_create_or_choose_a_host(resolve_editor, resolve_source, overrides):
    assert resolve_editor.post(PREFIX + '/register', json={**resolve_source, **overrides}).status_code == 422
    assert not list_jobs()


def test_interpreted_rate_is_rejected_before_creating_or_rebinding(resolve_editor, resolve_source):
    body = {**resolve_source, 'source_fps': 24}
    assert resolve_editor.post(PREFIX + '/register', json=body).status_code == 422
    assert not list_jobs()
    first = register(resolve_editor, resolve_source, host='premiere')
    assert resolve_editor.post(PREFIX + '/register', json=body).status_code == 422
    assert not editor_bridge.editor_sources(first['job_id'], host='resolve')
    assert len(list_jobs()) == 1


def test_internal_host_whitelist_rejects_invalid_hosts_before_registration(resolve_editor, resolve_source):
    body = editor_bridge.EditorSource(**resolve_source)
    assert editor_bridge.PremiereSource is editor_bridge.EditorSource
    with pytest.raises(ValueError, match='Unsupported editor host'):
        asyncio.run(editor_bridge.register_source(body, host="resolve' OR 1=1 --"))
    with pytest.raises(ValueError, match='Unsupported editor host'):
        editor_bridge.project_assets(resolve_source['project_id'], host='other')
    assert not list_jobs()


def test_concurrent_registration_shares_one_asset_between_hosts(resolve_editor, resolve_source):
    body = editor_bridge.EditorSource(**resolve_source)

    async def scenario():
        return await asyncio.gather(editor_bridge.register_source(body, host='resolve'),
                                    editor_bridge.register_source(body, host='premiere'))

    resolve, premiere = asyncio.run(scenario())
    assert resolve['job_id'] == premiere['job_id'] and len(list_jobs()) == 1
    assert {s['host'] for s in editor_bridge.editor_sources(resolve['job_id'])} == {'resolve', 'premiere'}


def test_analysis_is_explicit_and_delivers_searchable_shots_and_dialogue(resolve_editor, resolve_source, monkeypatch):
    asset = register(resolve_editor, resolve_source)
    path = PREFIX + f"/assets/{asset['job_id']}"
    assert resolve_editor.post(path + '/analyze', json={'mode': 'flash_pro'}).status_code == 400
    assert get_job(asset['job_id']).status == 'ready'
    settings.google_api_key = 'test-provider-key'
    calls = []
    from app.routers import library
    monkeypatch.setattr(library, 'start_job', lambda ident, request, key: calls.append((ident, request.mode, key)))
    assert resolve_editor.post(path + '/analyze', json={'mode': 'flash_pro'}).status_code == 202
    assert calls == [(asset['job_id'], 'flash_pro', None)]
    assert resolve_editor.post(path + '/analyze', json={'mode': 'flash_only'}).status_code == 409
    update_job(asset['job_id'], status='analyzing', file_id='private-provider-id', file_uri='private-provider-uri',
               analysis_progress={'completed_sections': 1, 'total_sections': 2},
               flash_result={'scenes': [{'scene_number': 1, 'shots': [{
                   'shot_number': 1, 'start_time': '00:00.000', 'end_time': '00:03.000',
                   'visual_description': 'A bright blue circle', 'tags': ['blue', 'circle'],
               }]}]}, transcript=[{'start_seconds': 1, 'end_seconds': 2, 'text': 'Hello sailor'}])
    detail = resolve_editor.get(path)
    assert detail.json()['analysis_progress']['completed_sections'] == 1
    for secret in ('private-provider-id', 'private-provider-uri', 'test-provider-key'):
        assert secret not in detail.text
    for query, matched_source in [('sailor', 'Transcript'), ('circle', 'Shot metadata')]:
        result = resolve_editor.get(path + '/shots', params={'q': query}).json()
        assert result['total'] == 1 and matched_source in result['shots'][0]['match_sources']
    srt = resolve_editor.get(path + '/subtitles')
    assert srt.status_code == 200 and 'Hello sailor' in srt.text
    assert '00:00:01,000 --> 00:00:02,000' in srt.text
    assert 'Original%20Resolve%20film.srt' in srt.headers['content-disposition']


def test_subtitles_never_turn_shot_descriptions_into_dialogue(resolve_editor, resolve_source):
    asset = register(resolve_editor, resolve_source)
    update_job(asset['job_id'], flash_result={'scenes': [{'shots': [{
        'shot_number': 1, 'start_time': '00:00.000', 'end_time': '00:03.000',
        'visual_description': 'A person speaking', 'audio_notes': 'Says something',
    }]}]})
    response = resolve_editor.get(PREFIX + f"/assets/{asset['job_id']}/subtitles")
    assert response.status_code == 422
    assert 'No timed transcript' in response.json()['detail']


def test_desktop_settings_share_private_companion_configuration(resolve_editor, monkeypatch):
    assert resolve_editor.post(PREFIX + '/settings', json={'google_api_key': 'test-secret'}).status_code == 409
    monkeypatch.setenv('VIDEO_ANALYZER_DESKTOP', '1')
    response = resolve_editor.post(PREFIX + '/settings', json={'google_api_key': 'test-secret'})
    assert response.status_code == 200 and response.json()['google_configured']
    assert 'test-secret' not in response.text
    saved = Path(settings.database_path).parent / 'desktop-settings.json'
    assert json.loads(saved.read_text()) == {'google_api_key': 'test-secret'}
    assert resolve_editor.get('/api/editor/premiere/health').json()['google_configured']
