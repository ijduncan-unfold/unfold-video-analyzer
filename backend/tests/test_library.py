import asyncio
import importlib
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.config import settings
from app.services import job_store


def test_durable_storage_reload_and_metadata_merge(client, asset):
    response = client.patch('/api/library/test-asset', json={'campaign': 'Launch', 'tags': [' Boat ', 'boat', 'Blue']})
    assert response.status_code == 200
    assert response.json()['metadata']['client'] == 'Studio'
    importlib.reload(job_store)
    restored = client.get('/api/library/test-asset').json()
    assert restored['metadata']['campaign'] == 'Launch'
    assert restored['metadata']['tags'] == ['Boat', 'Blue']
    assert 'file_uri' not in restored and 'local_path' not in restored


def test_search_filters_and_shot_annotations(client, asset):
    assert client.get('/api/library?q=sailboat').json()['total'] == 1
    assert client.get('/api/library?q=missing').json()['total'] == 0
    assert client.get('/api/library?q=camera_movement').json()['total'] == 0
    assert client.get('/api/library?project=Other').json()['total'] == 0
    assert client.get('/api/library/search/shots?q=blue+water').json()['total'] == 1
    assert client.get('/api/library/search/shots?q=rope+water').json()['total'] == 0
    response = client.patch('/api/library/test-asset/shots/1', json={'tags': ['hero'], 'review_status': 'reviewed', 'notes': 'Best take'})
    assert response.status_code == 200
    shot = client.get('/api/library/search/shots?q=hero&review_status=reviewed').json()['shots'][0]
    assert shot['end_seconds'] == 3.5
    assert shot['human_tags'] == ['hero']
    assert 'boat' in shot['tags']
    assert client.patch('/api/library/test-asset/shots/99', json={'notes': 'x'}).status_code == 404


def test_metadata_rejects_invalid_and_private_fields(client, asset):
    assert client.patch('/api/library/test-asset', json={'rights_status': 'licensed_guess'}).status_code == 422
    assert client.patch('/api/library/test-asset', json={'file_uri': 'bad'}).status_code == 422
    assert client.patch('/api/library/test-asset', json={'tags': ['x' * 101]}).status_code == 422
    assert client.get('/api/library/absent').status_code == 404


def test_key_missing_and_duplicate_analysis(client, asset):
    assert client.post('/api/library/test-asset/analyze', json={}).status_code == 400
    assert client.post('/api/library/test-asset/analyze?api_key=never-read-query-keys', json={}).status_code == 400
    assert client.post('/api/library/test-asset/analyze', json={'fps': 24}).status_code == 422
    settings.google_api_key = 'test-not-a-real-key'
    with patch('app.routers.library.start_job') as start:
        assert client.post('/api/library/test-asset/analyze', json={}).status_code == 202
        assert client.post('/api/library/test-asset/analyze', json={}).status_code == 409
        assert start.call_count == 1
    assert client.delete('/api/files/test-asset').status_code == 409


def test_restart_recovers_interrupted_jobs(client, asset):
    job_store.update_job(asset.job_id, status='analyzing')
    job_store.recover_interrupted_jobs()
    restored = job_store.get_job(asset.job_id)
    assert restored.status == 'error'
    assert restored.flash_result is not None
    assert 'restart' in restored.error


def test_local_upload_probe_poster_and_range_without_ai_key(client, tmp_path):
    path = tmp_path / 'fixture.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=320x180:r=25',
                    '-t', '1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-y', str(path)], check=True)
    with path.open('rb') as file:
        response = client.post('/api/upload', files={'file': ('Fixture clip.mp4', file, 'video/mp4')})
    assert response.status_code == 200, response.text
    job_id = response.json()['job_id']
    detail = client.get(f'/api/library/{job_id}').json()
    assert detail['technical']['frame_rate'] == 25
    assert detail['technical']['width'] == 320
    assert client.get(detail['thumbnail_url']).status_code == 200
    ranged = client.get(detail['preview_url'], headers={'Range': 'bytes=0-31'})
    assert ranged.status_code == 206 and len(ranged.content) == 32
    assert job_store.get_job(job_id).file_id == ''


def test_invalid_empty_and_oversize_upload_cleanup(client):
    assert client.post('/api/upload', files={'file': ('bad.mp4', b'', 'video/mp4')}).status_code == 400
    assert client.post('/api/upload', files={'file': ('bad.mp4', b'not-video', 'video/mp4')}).status_code == 400
    original = settings.max_file_size_mb
    try:
        settings.max_file_size_mb = 0
        assert client.post('/api/upload', files={'file': ('big.mp4', b'x', 'video/mp4')}).status_code == 413
    finally:
        settings.max_file_size_mb = original
    assert not list(Path(settings.upload_dir).glob('*.mp4'))


def test_background_runner_persists_progress(client, asset):
    from app.models.library import AnalyzeRequest
    from app.services import job_runner
    async def fake_analysis(job, *args, **kwargs):
        yield {'event': 'pass_start', 'data': '{"name":"Test analysis"}'}
        job_store.update_job(job.job_id, status='complete', analysis_config={'provider': 'gemini'})
        yield {'event': 'analysis_complete', 'data': '{}'}
    with patch.object(job_runner, 'ensure_remote_file', new=AsyncMock()), patch.object(job_runner, 'run_analysis', fake_analysis):
        asyncio.run(job_runner._run(asset.job_id, AnalyzeRequest(), None))
    result = job_store.get_job(asset.job_id)
    assert result.status == 'complete'
    assert result.analysis_config['provider'] == 'gemini'
    assert 'review' in result.progress


def test_api_capabilities_do_not_expose_secret(client):
    settings.google_api_key = 'private-secret-test'
    response = client.get('/api/capabilities')
    assert response.json()['google_configured'] is True
    assert 'private-secret-test' not in response.text
