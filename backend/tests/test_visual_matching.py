import asyncio
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.services import visual_index
from app.services.job_store import update_job, get_job
from app.services.visual_features import describe, compare


def picture(color=(0, 0, 255), circle=True, center=(160, 90)):
    image = np.full((180, 320, 3), 20, np.uint8)
    if circle:
        cv2.circle(image, center, 38, color, -1)
    else:
        cv2.rectangle(image, (center[0] - 48, center[1] - 20), (center[0] + 48, center[1] + 20), color, -1)
    return image


def test_shape_matches_different_color_and_composition_distinguishes_position():
    source = describe(picture())
    circle = describe(picture(color=(255, 150, 0)))
    rectangle = describe(picture(circle=False, center=(65, 60)))
    moved = describe(picture(center=(65, 60)))
    weights = {'shape': 1, 'composition': 0, 'color': 0}
    assert compare(source, circle, weights)['score'] > compare(source, rectangle, weights)['score']
    weights = {'shape': 0, 'composition': 1, 'color': 0}
    assert compare(source, source, weights)['score'] > compare(source, moved, weights)['score']
    weights = {'shape': 0, 'composition': 0, 'color': 1}
    assert compare(source, source, weights)['score'] > compare(source, circle, weights)['score']
    assert compare(source, circle, weights)['source_box'] is None  # Color-only results must not show contour boxes.


def test_blank_and_region():
    assert not describe(np.zeros((180, 320, 3), np.uint8))['usable']
    features = describe(picture())
    result = compare(features, features, {'shape': 1, 'composition': 1, 'color': 0}, [0, 0, .1, .1])
    assert result['source_box'] is None


def test_shape_preserves_portrait_proportions():
    landscape = describe(picture())
    portrait_image = np.full((320, 180, 3), 20, np.uint8)
    cv2.circle(portrait_image, (90, 160), 38, (0, 0, 255), -1)
    portrait = describe(portrait_image)
    assert portrait['aspect_ratio'] == 180 / 320
    result = compare(landscape, portrait, {'shape': 1, 'composition': 0, 'color': 0})
    assert result['score'] > .95


@pytest.fixture
def visual_asset(asset, monkeypatch, tmp_path):
    path = tmp_path / 'original.mp4'
    path.write_bytes(b'fixture')
    update_job(asset.job_id, local_path=str(path))
    async def extract(_, seconds, target):
        await asyncio.sleep(.005)
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(target, picture(color=(0, 0, 255) if seconds < 3.5 else (255, 150, 0)))
        return True
    monkeypatch.setattr(visual_index, '_extract_frame', extract)
    return get_job(asset.job_id)


def wait_index(client, ident):
    for _ in range(100):
        status = client.get(f'/api/visual/{ident}/index').json()
        if status['status'] != 'indexing':
            return status
        time.sleep(.02)
    pytest.fail('index did not finish')


def test_index_search_resume_and_stale_guards(client, visual_asset):
    ident = visual_asset.job_id
    before = get_job(ident)
    assert client.post(f'/api/visual/{ident}/index').status_code == 202
    state = wait_index(client, ident)
    assert state['status'] == 'complete'
    assert state['indexed'] == 10
    payload = {'shot_number': 1, 'seconds': 1.5, 'revision': state['revision'], 'target_ids': [ident], 'composition': 0, 'shape': 0}
    response = client.post(f'/api/visual/{ident}/search', json=payload)
    assert response.status_code == 200, response.text
    matches = response.json()['matches']
    assert len(matches) == 1
    assert matches[0]['shot_number'] == 2
    assert matches[0]['source_box'] is None
    assert client.get(matches[0]['frame_url']).headers['content-type'] == 'image/jpeg'
    client.post(f'/api/visual/{ident}/index')
    assert wait_index(client, ident)['indexed'] == 10
    assert get_job(ident) == before  # local matching does not rewrite analysis or provider usage
    update_job(ident, analysis_config={'started_at': 'new-run'})
    assert client.post(f'/api/visual/{ident}/search', json=payload).status_code == 409
    assert client.get(f'/api/visual/{ident}/index').json()['indexed'] == 0


def test_validation_and_cancel(client, visual_asset):
    ident = visual_asset.job_id
    revision = client.get(f'/api/visual/{ident}/index').json()['revision']
    base = {'shot_number': 1, 'seconds': 1, 'revision': revision, 'target_ids': [ident], 'composition': 0, 'shape': 0}
    for changes in [{'target_ids': []}, {'shape': 0, 'composition': 0, 'color': 0}, {'region': [0, 0, 3, 1]}]:
        assert client.post(f'/api/visual/{ident}/search', json={**base, **changes}).status_code == 422
    for changes in [{'seconds': 100}, {'shot_number': 100}]:
        assert client.post(f'/api/visual/{ident}/search', json={**base, **changes}).status_code == 409
    assert client.post(f'/api/visual/{ident}/search', json={**base, 'target_ids': ['missing']}).status_code == 404
    client.post(f'/api/visual/{ident}/index')
    assert client.post(f'/api/visual/{ident}/index/cancel').status_code == 200
    state = client.get(f'/api/visual/{ident}/index').json()
    assert state['status'] != 'indexing'
    client.post(f'/api/visual/{ident}/index')
    assert wait_index(client, ident)['status'] == 'complete'


def test_no_local_media_or_active_analysis(client, asset):
    assert client.post(f'/api/visual/{asset.job_id}/index').status_code == 409
    update_job(asset.job_id, status='analyzing')
    assert client.get(f'/api/visual/{asset.job_id}/index').status_code == 409


def test_match_between_distinct_single_take_media_without_splitting_recordings(client, visual_asset):
    import copy
    from app.services.job_store import create_job
    from app.services.library_folders import save_folder
    source = get_job(visual_asset.job_id)
    result = copy.deepcopy(source.flash_result)
    result['scenes'][0]['shots'] = [result['scenes'][0]['shots'][0]]
    result['scenes'][0]['shots'][0]['end_time'] = '00:08.000'
    update_job(source.job_id, flash_result=result)
    source = get_job(source.job_id)
    target = copy.deepcopy(source)
    target.job_id = 'other-single-take'
    target.filename = 'Another recording.mov'
    target_path = Path(source.local_path).with_name('other-recording.mp4')
    target_path.write_bytes(b'different original recording')
    target.local_path = str(target_path)
    folder = save_folder('Second camera')
    target.folder_id = folder['id']
    create_job(target)
    for ident in [source.job_id, target.job_id]:
        assert client.post(f'/api/visual/{ident}/index').status_code == 202
        assert wait_index(client, ident)['status'] == 'complete'
    revision = client.get(f'/api/visual/{source.job_id}/index').json()['revision']
    response = client.post(f'/api/visual/{source.job_id}/search', json={
        'shot_number': 1, 'seconds': 1, 'revision': revision, 'target_ids': [source.job_id, target.job_id],
        'shape': 0, 'composition': 0, 'color': 1})
    assert response.status_code == 200, response.text
    assert response.json()['matches'][0]['job_id'] == target.job_id
    assert response.json()['matches'][0]['shot_number'] == 1
    projects = client.get('/api/library/projects').json()['projects']
    row = next(item for item in projects if item['id'] == target.job_id)
    assert row['folder_id'] == folder['id'] and row['has_local_media'] is True
    assert row['shot_count'] == 1
