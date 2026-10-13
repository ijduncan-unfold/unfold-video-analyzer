import json
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app

from app.services.editor_bridge import get_pairing_token, source_fingerprint
from app.services.job_store import update_job


def ready(asset, tmp_path):
    path = tmp_path / 'Wonder Woman.MP4'
    path.write_bytes(b'original footage fixture')
    fingerprint, _, _ = source_fingerprint(str(path))
    flash = asset.flash_result
    flash['scenes'][0]['shots'][0]['cosplay'] = [{'character': 'Wonder Woman', 'franchise': 'DC', 'identification': 'suggested'}]
    return update_job(asset.job_id, local_path=str(path), media_origin='external', source_fingerprint=fingerprint,
                      flash_result=flash, analysis_config={'mode': 'cosplay'},
                      technical={**asset.technical, 'nominal_frame_rate_verified': False, 'variable_frame_rate': True})


def test_saved_selection_imports_whole_unverified_sources_with_latest_metadata(client, asset, tmp_path):
    job = ready(asset, tmp_path)
    result = client.post('/api/library/premiere-selections', json={'name': ' Wonder Woman ', 'job_ids': [job.job_id]})
    assert result.status_code == 201
    selection = result.json()
    assert selection['name'] == 'Wonder Woman' and selection['count'] == 1
    assert client.get('/api/library/premiere-selections').json()['selections'] == [selection]
    assert job.local_path not in json.dumps(selection)
    base = '/api/editor/premiere/selections/' + selection['id']
    assert client.get(base + '/plan').status_code == 403
    client = TestClient(app, client=('127.0.0.1', 52000))
    assert client.get(base + '/plan').status_code == 401
    headers = {'Authorization': 'Bearer ' + get_pairing_token()}
    plan = client.get(base + '/plan', headers=headers).json()
    assert plan['items'] == [{'id': job.job_id, 'filename': job.filename, 'media_path': job.local_path}]
    assert plan['schema'] == 'unfold.premiere-selection'
    update_job(job.job_id, shot_annotations={'1': {'cosplay': [{'character': 'Green Lantern', 'franchise': 'DC', 'identification': 'confirmed'}]}})
    metadata = client.get(base + '/items/' + job.job_id, headers=headers).json()
    fields = {field['key']: field['value'] for field in metadata['mapping']['fields']}
    assert 'Green Lantern' in fields['description'] and 'Wonder Woman' not in fields['description']
    assert 'Green Lantern' in fields['keywords']
    assert client.get(base + '/items/other-job', headers=headers).status_code == 404
    # No analysis or timed XML is needed; real source changes are still rejected.
    Path(job.local_path).write_bytes(b'changed source')
    assert client.get(base + '/plan', headers=headers).status_code == 409
    assert client.get(base + '/items/' + job.job_id, headers=headers).status_code == 409


def test_selection_validation_and_offline_sources(client, asset, tmp_path):
    job = ready(asset, tmp_path)
    for body in ({'name': ' ', 'job_ids': [job.job_id]}, {'name': 'A', 'job_ids': []},
                 {'name': 'A', 'job_ids': [job.job_id, job.job_id]}, {'name': 'A', 'job_ids': [job.job_id] * 1001}):
        assert client.post('/api/library/premiere-selections', json=body).status_code == 422
    assert client.post('/api/library/premiere-selections', json={'name': 'Gone', 'job_ids': ['missing']}).status_code == 409
    Path(job.local_path).unlink()
    assert client.post('/api/library/premiere-selections', json={'name': 'Gone', 'job_ids': [job.job_id]}).status_code == 409
    assert client.get('/api/library/premiere-selections').json()['selections'] == []
