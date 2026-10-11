from dataclasses import replace
import json

from app.services import job_store


def test_folder_lifecycle_preserves_originals_and_analysis(client, asset, tmp_path):
    original = tmp_path / 'original.mxf'
    original.write_bytes(b'camera original')
    job_store.update_job(asset.job_id, local_path=str(original), media_origin='external', status='analyzing')
    other = job_store.create_job(replace(asset, job_id='other', filename='other.mov'))
    created = client.post('/api/library/folders', json={'name': '  Day 1  '})
    assert created.status_code == 201
    folder = created.json()
    assert folder['name'] == 'Day 1'
    assert client.post('/api/library/folders', json={'name': 'DAY 1'}).status_code == 409
    assert client.post('/api/library/move', json={'job_ids': [asset.job_id, other.job_id], 'folder_id': folder['id']}).json()['moved'] == 2
    # Re-open the database: folders and membership are not session state.
    listing = client.get('/api/library/folders').json()
    assert listing == {'folders': [{**folder, 'shot_count': 2}], 'total_shots': 2, 'unfiled_shots': 0}
    assert job_store.get_job(asset.job_id).flash_result == asset.flash_result
    assert job_store.get_job(asset.job_id).status == 'analyzing'
    assert client.patch(f"/api/library/folders/{folder['id']}", json={'name': 'Interiors'}).status_code == 200
    assert job_store.get_job(asset.job_id).folder_id == folder['id']
    assert client.get('/api/library', params={'folder_id': folder['id']}).json()['total'] == 2
    assert client.get('/api/library', params={'folder_id': 'unfiled'}).json()['total'] == 0
    assert client.get('/api/library/search/shots', params={'folder_id': folder['id']}).json()['total'] == 4
    assert client.get('/api/library/projects', params={'folder_id': folder['id']}).json()['total'] == 2
    detail = client.get(f'/api/library/{asset.job_id}').json()
    assert detail['folder_id'] == folder['id']
    assert client.delete(f"/api/library/folders/{folder['id']}").status_code == 200
    assert client.get('/api/library/folders').json()['unfiled_shots'] == 2
    assert client.get('/api/library', params={'folder_id': 'unfiled'}).json()['total'] == 2
    assert job_store.get_job(asset.job_id).flash_result == asset.flash_result
    assert original.read_bytes() == b'camera original'


def test_bulk_move_is_atomic_and_can_return_to_unfiled(client, asset):
    folder = client.post('/api/library/folders', json={'name': 'Selects'}).json()
    assert client.post('/api/library/move', json={'job_ids': [asset.job_id, 'missing'], 'folder_id': folder['id']}).status_code == 404
    assert job_store.get_job(asset.job_id).folder_id is None
    assert client.post('/api/library/move', json={'job_ids': [asset.job_id], 'folder_id': 'missing'}).status_code == 404
    assert client.post('/api/library/move', json={'job_ids': [asset.job_id, asset.job_id], 'folder_id': folder['id']}).json()['moved'] == 1
    assert client.post('/api/library/move', json={'job_ids': [asset.job_id], 'folder_id': None}).status_code == 200
    assert job_store.get_job(asset.job_id).folder_id is None
    job_store.update_job(asset.job_id, status='deleting')
    assert client.post('/api/library/move', json={'job_ids': [asset.job_id], 'folder_id': folder['id']}).status_code == 409


def test_validation_and_legacy_jobs(client, asset):
    for name in ('', '   ', 'a' * 101):
        assert client.post('/api/library/folders', json={'name': name}).status_code == 422
    assert client.post('/api/library/move', json={'job_ids': []}).status_code == 422
    assert client.delete('/api/library/folders/missing').status_code == 404
    assert client.patch('/api/library/folders/missing', json={'name': 'Name'}).status_code == 404
    first = client.post('/api/library/folders', json={'name': 'First'}).json()
    second = client.post('/api/library/folders', json={'name': 'Second'}).json()
    assert client.patch(f"/api/library/folders/{second['id']}", json={'name': 'FIRST'}).status_code == 409
    assert client.get('/api/library/folders').json()['folders'][0]['name'] == first['name']
    # Existing databases have jobs without folder_id; no migration rewrites media or analysis.
    with job_store.connection() as db:
        raw = json.loads(db.execute('SELECT data FROM jobs WHERE job_id=?', (asset.job_id,)).fetchone()[0])
        raw.pop('folder_id')
        db.execute('UPDATE jobs SET data=? WHERE job_id=?', (json.dumps(raw), asset.job_id))
    assert job_store.get_job(asset.job_id).folder_id is None
    assert client.get('/api/library/folders').json()['unfiled_shots'] == 1
