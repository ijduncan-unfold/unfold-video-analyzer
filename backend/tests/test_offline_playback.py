from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException

from app.config import settings
from app.services.camera_media import browser_media
from app.services.editor_bridge import source_fingerprint, ensure_source_unchanged
from app.services.job_store import Job, create_job


def recording(tmp_path, viewing=True, codec='h264'):
    original = tmp_path / 'recording.mxf'
    original.write_bytes(b'original recording')
    proxy = Path(settings.upload_dir) / 'offline-test' / 'sony-viewing.mp4'
    proxy.parent.mkdir(parents=True, exist_ok=True)
    proxy.write_bytes(b'cached local viewing media')
    return create_job(Job(job_id='offline-test', filename=original.name, file_id='', file_uri='',
        size_bytes=original.stat().st_size, mime_type='application/mxf', local_path=str(original),
        media_origin='external', source_fingerprint=source_fingerprint(original)[0],
        viewing_path=str(proxy) if viewing else '', viewing_fingerprint=source_fingerprint(proxy)[0] if viewing else '',
        technical={'codec': 'rawvideo', 'playback_proxy_required': True,
                   'viewing_proxy': {'codec': codec, 'generated': True}})), original, proxy


def test_cached_camera_proxy_streams_with_offline_original_and_keeps_strict_analysis_guard(client, tmp_path):
    job, original, proxy = recording(tmp_path)
    original.rename(original.with_suffix('.offline'))
    response = client.get(f'/api/library/{job.job_id}/media', headers={'Range': 'bytes=0-5'})
    assert response.status_code == 206
    assert response.content == proxy.read_bytes()[:6]
    status = client.get(f'/api/library/{job.job_id}/playback-status').json()
    assert status['available'] and status['original_offline'] and status['kind'] == 'viewing_proxy'
    assert 'offline' in status['message']
    assert 'path' not in status and str(tmp_path) not in str(status)
    with pytest.raises(HTTPException, match='original media is offline'):
        ensure_source_unchanged(job)


@pytest.mark.parametrize('missing_viewing', [False, True])
def test_existing_playback_cache_works_offline_without_encoder(client, tmp_path, missing_viewing):
    job, original, proxy = recording(tmp_path, codec='prores')
    original.rename(original.with_suffix('.offline'))
    if missing_viewing:
        proxy.unlink()
    playback = proxy.with_name('playback.mp4')
    playback.write_bytes(b'full length browser playback')
    with patch('app.services.camera_media.asyncio.create_subprocess_exec', AsyncMock()) as encode:
        result = client.get(f'/api/library/{job.job_id}/media')
    assert result.status_code == 200 and result.content == playback.read_bytes()
    encode.assert_not_called()
    assert client.get(f'/api/library/{job.job_id}/playback-status').json()['kind'] == 'cached_proxy'


def test_offline_original_without_valid_proxy_has_correct_error_and_recovers_on_return(client, tmp_path):
    job, original, proxy = recording(tmp_path, viewing=False)
    proxy.with_name('playback.mp4').write_bytes(b'')
    offline = original.with_suffix('.offline')
    original.rename(offline)
    result = client.get(f'/api/library/{job.job_id}/media')
    assert result.status_code == 404
    assert 'Original media is offline' in result.json()['detail']
    assert 'no cached playback proxy' in result.json()['detail']
    status = client.get(f'/api/library/{job.job_id}/playback-status').json()
    assert not status['available'] and status['message'] == result.json()['detail']
    offline.rename(original)
    assert client.get(f'/api/library/{job.job_id}/playback-status').json()['available']


@pytest.mark.parametrize('changed', ['original', 'proxy'])
def test_changed_media_is_still_rejected_even_with_cached_playback(client, tmp_path, changed):
    job, original, proxy = recording(tmp_path)
    proxy.with_name('playback.mp4').write_bytes(b'cached browser proxy')
    if changed == 'original':
        original.write_bytes(b'different original recording')
    else:
        original.rename(original.with_suffix('.offline'))
        proxy.write_bytes(b'different proxy recording')
    result = client.get(f'/api/library/{job.job_id}/media')
    assert result.status_code == 409 and 'changed' in result.json()['detail']


def test_missing_viewing_proxy_is_not_misreported_as_missing_original(client, tmp_path):
    job, original, proxy = recording(tmp_path)
    proxy.unlink()
    result = client.get(f'/api/library/{job.job_id}/media')
    assert result.status_code == 404
    assert result.json()['detail'].startswith('The viewing proxy is missing or offline')
    assert original.read_bytes() == b'original recording'


def test_browser_proxy_can_be_created_from_local_viewing_copy_while_nas_is_offline(client, tmp_path):
    job, original, proxy = recording(tmp_path, codec='prores')
    original.rename(original.with_suffix('.offline'))
    async def encode(*args, **kwargs):
        assert args[args.index('-i') + 1] == str(proxy)
        Path(args[-1]).write_bytes(b'encoded browser proxy')
        return Mock(returncode=0, communicate=AsyncMock(return_value=(b'', b'')))
    with patch('app.services.camera_media.asyncio.create_subprocess_exec', side_effect=encode):
        result = client.get(f'/api/library/{job.job_id}/media')
    assert result.status_code == 200 and result.content == b'encoded browser proxy'
    assert proxy.read_bytes() == b'cached local viewing media'
    assert client.get(f'/api/library/{job.job_id}/playback-status').json()['kind'] == 'cached_proxy'
