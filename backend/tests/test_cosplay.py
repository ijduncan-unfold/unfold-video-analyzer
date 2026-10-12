"""Cosplay catalog contracts. Synthetic metadata and mocked Gemini; no paid calls."""
import asyncio
import csv
import io
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models.analysis import Shot
from app.models.cosplay import CosplayIdentification
from app.services import analyzer, flash_pass
from app.services.analysis_support import processing_sections
from app.services.job_store import create_job, get_job, update_job
from app.services.shot_continuity import stitch_section


def costume(character='Spiderman', franchise='Marvel Comics', status='suggested', **kwargs):
    return CosplayIdentification(character=character, franchise=franchise,
        identification=status, visible_details='Black-and-red suit, web pattern, mask',
        activity='Posing', **kwargs).model_dump()


@pytest.fixture
def catalog(asset):
    result = asset.flash_result
    result['scenes'][0]['shots'][0]['cosplay'] = [costume(), costume('Batman', 'DC')]
    result['scenes'][0]['shots'][1]['cosplay'] = [costume('', '', 'unknown', possible_characters=['Goku'])]
    return update_job(asset.job_id, flash_result=result, analysis_config={'mode': 'cosplay', 'started_at': 'run-one'})


def test_same_costume_filters_aliases_and_group_members(client, catalog):
    for params in ({'character': 'spiderman'}, {'character': 'SPIDER-MAN', 'franchise': 'Marvel'},
                   {'character': 'Batman', 'franchise': 'DC Comics'}):
        assert client.get('/api/library', params=params).json()['total'] == 1
        assert client.get('/api/library/search/shots', params=params).json()['total'] == 1
    for endpoint in ('/api/library', '/api/library/search/shots'):
        assert client.get(endpoint, params={'character': 'Spider-Man', 'franchise': 'DC'}).json()['total'] == 0
        assert client.get(endpoint, params={'character': 'Goku'}).json()['total'] == 0
        assert client.get(endpoint, params={'identification': 'confirmed'}).json()['total'] == 0
    assert client.get('/api/library/search/shots', params={'identification': 'unknown'}).json()['total'] == 1
    assert client.get('/api/library', params={'q': 'spiderman'}).json()['total'] == 1
    assert client.get('/api/library/search/shots', params={'q': 'spiderman'}).json()['total'] == 1
    assert client.get('/api/library', params={'q': 'red mask'}).json()['total'] == 1


def test_corrections_drive_search_facets_and_exports_and_preserve_raw(client, catalog):
    url = f'/api/library/{catalog.job_id}/shots/1'
    edited = [costume('Deadpool', 'Marvel', 'confirmed')]
    assert client.patch(url, json={'cosplay': edited}).status_code == 200
    # Existing generic annotation edits must not erase cosplay corrections.
    assert client.patch(url, json={'notes': 'Checked costume'}).status_code == 200
    assert client.get('/api/library', params={'character': 'Spider-Man'}).json()['total'] == 0
    assert client.get('/api/library', params={'q': 'spiderman'}).json()['total'] == 0
    matches = client.get('/api/library/search/shots', params={'character': 'Deadpool', 'identification': 'confirmed'}).json()
    assert matches['total'] == 1
    assert matches['shots'][0]['cosplay'] == edited
    assert client.get('/api/library').json()['facets']['character'] == ['Deadpool']
    stored = get_job(catalog.job_id)
    assert stored.flash_result['scenes'][0]['shots'][0]['cosplay'][0]['character'] == 'Spider-Man'
    document = client.get(f'/api/library/{catalog.job_id}/export?format=json').json()
    assert document['shot_annotations']['1']['cosplay'] == edited
    response = client.get(f'/api/library/{catalog.job_id}/export?format=csv')
    rows = list(csv.DictReader(io.StringIO(response.text.lstrip('\ufeff'))))
    assert rows[0]['Cosplay Characters'] == 'Deadpool'
    assert json.loads(rows[0]['Cosplay Identifications']) == edited


def test_clear_restore_and_stale_review_guard(client, catalog):
    url = f'/api/library/{catalog.job_id}/shots/1'
    response = client.patch(url, params={'expected_analysis_started_at': 'older-run'}, json={'cosplay': []})
    assert response.status_code == 409
    assert client.patch(url, json={'cosplay': []}).status_code == 200
    assert client.get('/api/library', params={'character': 'Spider-Man'}).json()['total'] == 0
    assert client.patch(url, json={'cosplay': None}).status_code == 200
    assert client.get('/api/library', params={'character': 'Spider-Man'}).json()['total'] == 1
    # Unknown moves a suggested character into alternatives, never a confirmed filter match.
    assert client.patch(url, json={'cosplay': [{'character': 'Batman', 'identification': 'unknown'}]}).status_code == 200
    assert client.get('/api/library', params={'character': 'Batman'}).json()['total'] == 0
    assert client.patch(url, json={'cosplay': [{'identification': 'approved'}]}).status_code == 422


def test_folder_facets_and_case_deduplication(client, catalog):
    create_job(replace(catalog, job_id='other', folder_id='other-folder'))
    update_job(catalog.job_id, folder_id='comic-con', shot_annotations={'1': {'cosplay': [costume('spider man', 'marvel')]}})
    facets = client.get('/api/library', params={'folder_id': 'comic-con'}).json()['facets']
    assert facets['character'] == ['Spider-Man']
    assert facets['franchise'] == ['Marvel']
    assert client.get('/api/library', params={'folder_id': 'unfiled'}).json()['facets']['character'] == []


@pytest.mark.parametrize('mode', ['cosplay', 'flash_only'])
def test_model_output_cannot_confirm_and_cosplay_is_opt_in(monkeypatch, mode):
    shot = Shot(shot_number=1, start_time='00:00.000', end_time='00:15.000', shot_type='wide',
        camera_movement='static', visual_description='Costumes', audio_notes='',
        cosplay=[costume(status='confirmed')])
    generate = AsyncMock(return_value=SimpleNamespace(text=json.dumps({'shots': [shot.model_dump()]})))
    monkeypatch.setattr(flash_pass, '_generate', generate)
    monkeypatch.setattr(flash_pass, 'get_client', lambda key: object())
    shots, _ = asyncio.run(flash_pass.run_shot_detection('uri', 'video/mp4', processing_sections(15)[0],
        1, 1, single_take=True, cosplay=mode == 'cosplay'))
    assert ('COSPLAY CATALOG MODE' in generate.call_args.kwargs['contents'][1]) == (mode == 'cosplay')
    if mode == 'cosplay':
        assert shots[0].cosplay[0].identification == 'suggested'
        assert shots[0].cosplay[0].character == 'Spider-Man'
    else:
        assert shots[0].cosplay == []


def test_cosplay_run_is_single_take_without_deep_passes_and_survives_reload(client, catalog, monkeypatch):
    captured = []
    async def detect(uri, mime, section, offset, fps, **kwargs):
        captured.append(kwargs)
        return [Shot(shot_number=offset, start_time=section.start_time, end_time=section.end_time,
            shot_type='wide', camera_movement='static', visual_description='Masked costume',
            audio_notes='', cosplay=[costume()])], {'model': 'test', 'usage_available': False}
    monkeypatch.setattr(analyzer, 'run_shot_detection', detect)
    deep = AsyncMock(side_effect=AssertionError('Cosplay must skip the deep pass'))
    monkeypatch.setattr(analyzer, 'run_pro_scene', deep)
    monkeypatch.setattr(analyzer, 'run_summary_pass', deep)
    monkeypatch.setattr(analyzer, 'extract_thumbnails', AsyncMock())
    update_job(catalog.job_id, technical={'duration_seconds': 15, 'has_audio': False})
    async def run():
        return [event async for event in analyzer.run_analysis(get_job(catalog.job_id), mode='cosplay')]
    events = asyncio.run(run())
    assert events[-1]['event'] == 'analysis_complete'
    assert captured[0]['cosplay'] and captured[0]['single_take']
    saved = get_job(catalog.job_id)
    assert saved.status == 'complete'
    assert saved.analysis_config['mode'] == 'cosplay'
    assert saved.analysis_config['resolved_shot_mode'] == 'single_take'
    assert client.get('/api/library', params={'character': 'Spider-Man'}).json()['total'] == 1
    deep.assert_not_called()


def test_cosplay_request_enters_existing_queue(client, catalog, monkeypatch):
    from app.routers import library
    calls = []
    monkeypatch.setattr(library, 'start_job', lambda *args: calls.append(args))
    response = client.post(f'/api/library/{catalog.job_id}/analyze', json={'mode': 'cosplay'}, headers={'X-API-Key': 'test-key'})
    assert response.status_code == 202
    assert calls[0][1].mode == 'cosplay'
    assert client.post(f'/api/library/{catalog.job_id}/analyze', json={'mode': 'cosplay'}, headers={'X-API-Key': 'test-key'}).status_code == 409


def test_continuity_preserves_costumes_from_both_sections():
    base = dict(shot_number=1, start_time='00:00.000', end_time='00:30.000', shot_type='wide',
                camera_movement='static', visual_description='Group', audio_notes='')
    first = Shot(**base, cosplay=[costume()])
    second = Shot(**{**base, 'shot_number': 2, 'start_time': '00:30.000', 'end_time': '00:45.000'},
                  start_boundary='continuation', cosplay=[costume('Batman', 'DC')])
    stitch_section(first, [second])
    assert [item.character for item in first.cosplay] == ['Spider-Man', 'Batman']


def test_thousand_clip_pagination_keeps_all_character_matches(client, catalog):
    for index in range(999):
        create_job(replace(catalog, job_id=f'booth-{index:04}', filename=f'Booth-{index:04}.mp4'))
    ids = []
    for offset in (0, 500):
        result = client.get('/api/library', params={'character': 'spiderman', 'limit': 500, 'offset': offset, 'sort': 'name'}).json()
        assert result['total'] == 1000
        ids.extend(asset['job_id'] for asset in result['assets'])
    assert len(set(ids)) == 1000
    clips = client.get('/api/library/search/shots', params={'character': 'Batman', 'offset': 990}).json()
    assert clips['total'] == 1000 and len(clips['shots']) == 10


def test_character_aliases_preserve_ordinary_editorial_search():
    from app.services.library_service import search_score
    assert search_score('MCU', {'shot_type': 'MCU'}) >= 0
    assert search_score('MCU', {'franchise': 'Marvel'}) == -1
    assert search_score('spiderman', {'description': 'Spider-Man'}) >= 0
    assert search_score('Spider-Man', {'description': 'Spiderman'}) >= 0
