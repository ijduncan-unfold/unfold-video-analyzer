"""Proxy routing contracts: overrides, camera originals, timing and source safety."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.models.library import AnalyzeRequest
from app.models.rough_cut import RoughCutRequest
from app.services import job_runner, rough_cut, video_segments
from app.services.job_store import get_job, update_job
from app.services.proxy_policy import proxy_decision


def source_job(**changes):
    values = dict(youtube_url=None, local_path='camera.mov', filename='camera.mov',
                  size_bytes=1_000_000, technical={'duration_seconds': 60, 'codec': 'h264'})
    values.update(changes)
    return SimpleNamespace(**values)


@pytest.mark.parametrize('codec', ['prores', 'prores_raw', 'dnxhd', 'dnxhr', 'cfhd', 'rawvideo', 'v210', 'jpeg2000'])
def test_auto_detects_short_camera_originals_without_container_guessing(codec):
    job = source_job(technical={'duration_seconds': 60, 'codec': codec})
    assert proxy_decision(job)['proxy_used']
    assert 'camera format' in proxy_decision(job)['proxy_reason']
    assert not proxy_decision(source_job())['proxy_used']  # A .mov alone is not a heavy codec.


@pytest.mark.parametrize('filename', ['camera.BRAW', 'camera.R3D', 'camera.ari', 'camera.crm', 'camera.dng'])
def test_auto_recognizes_raw_file_extensions(filename):
    assert proxy_decision(source_job(filename=filename))['proxy_used']


def test_duration_size_bitrate_and_explicit_overrides():
    short = source_job()
    long = source_job(technical={'duration_seconds': 1800, 'codec': 'h264'})
    assert proxy_decision(short, 'always')['proxy_used']
    assert proxy_decision(long)['proxy_used']
    assert not proxy_decision(long, 'off')['proxy_used']
    assert proxy_decision(source_job(size_bytes=1024 ** 3 + 1))['proxy_used']
    # High-rate camera H.264/HEVC can be heavy despite a smaller file and short runtime.
    high_rate = source_job(size_bytes=375_000_000)
    assert '50 Mb/s' in proxy_decision(high_rate)['proxy_reason']
    assert not proxy_decision(source_job(size_bytes=374_999_999))['proxy_used']
    assert AnalyzeRequest().proxy_mode == RoughCutRequest().proxy_mode == 'auto'


def test_missing_duration_and_youtube_never_silently_override_always_or_off():
    unknown = source_job(technical={})
    assert not proxy_decision(unknown)['proxy_used']
    with pytest.raises(ValueError, match='measured'):
        proxy_decision(unknown, 'always')
    with pytest.raises(ValueError, match='measured'):
        proxy_decision(source_job(size_bytes=2 * 1024 ** 3, technical={}))
    assert not proxy_decision(unknown, 'off')['proxy_used']
    youtube = source_job(youtube_url='https://youtube.com/watch?v=example')
    assert proxy_decision(youtube)['upload_strategy'] == 'youtube_url'
    with pytest.raises(ValueError, match='local source'):
        proxy_decision(youtube, 'always')


@pytest.mark.parametrize('mode,codec,duration,expected', [
    ('always', 'h264', 60, True), ('off', 'prores', 3600, False),
    ('auto', 'prores', 60, True), ('auto', 'h264', 60, False),
])
def test_standard_worker_routes_selected_mode(asset, tmp_path, mode, codec, duration, expected):
    path = tmp_path / 'original.mov'; path.write_bytes(b'camera original')
    job = update_job(asset.job_id, local_path=str(path), technical={'codec': codec, 'duration_seconds': duration})
    proxy = Mock(close=AsyncMock())
    async def analyze(job, *args, **kwargs):
        assert kwargs['proxy_mode'] == mode
        assert ('media' in kwargs) == expected
        yield {'event': 'analysis_complete', 'data': '{}'}
    try:
        with patch.object(job_runner, 'SegmentedVideo', return_value=proxy) as factory, \
             patch.object(job_runner, 'ensure_remote_file', AsyncMock()) as original, \
             patch.object(job_runner, 'run_analysis', analyze):
            asyncio.run(job_runner._run(job.job_id, AnalyzeRequest(proxy_mode=mode), None))
            assert factory.call_count == int(expected)
            assert original.await_count == int(not expected)
            assert proxy.close.await_count == int(expected)
    finally:
        job_runner._gate = None
    assert path.read_bytes() == b'camera original'


def test_api_rejects_invalid_proxy_request_before_claiming_analysis(client, asset, tmp_path):
    path = tmp_path / 'original.mov'; path.write_bytes(b'original')
    update_job(asset.job_id, local_path=str(path), technical={})
    with patch.object(job_runner, 'start_job') as start:
        result = client.post(f'/api/library/{asset.job_id}/analyze', json={'proxy_mode': 'always'}, headers={'X-API-Key': 'test'})
        assert result.status_code == 400
        assert 'measured' in result.json()['detail']
        start.assert_not_called()
    assert get_job(asset.job_id).status not in ('queued', 'analyzing')
    assert client.post(f'/api/library/{asset.job_id}/analyze', json={'proxy_mode': 'invalid'}).status_code == 422
    assert client.post(f'/api/library/{asset.job_id}/rough-cut', json={'proxy_mode': 'always'}).status_code == 400


@pytest.mark.parametrize('mode,expected', [('auto', False), ('always', True), ('off', False)])
def test_rough_cut_records_same_policy_and_original_upload_keeps_source_times(asset, tmp_path, mode, expected):
    path = tmp_path / 'original.mov'; path.write_bytes(b'original')
    job = update_job(asset.job_id, local_path=str(path), technical={'duration_seconds': 240, 'has_audio': True, 'codec': 'h264'})
    draft = rough_cut.new_draft(job, RoughCutRequest(proxy_mode=mode))
    assert draft['proxy_used'] is expected
    assert draft['proxy_mode'] == mode
    if not expected:
        update_job(job.job_id, rough_cut=draft)
        media = rough_cut.OriginalCutMedia(job, None, draft)
        async def use():
            with patch.object(rough_cut, 'ensure_remote_file', AsyncMock(return_value=job)) as upload:
                assert await media.get(0, 120) == (job.file_uri, job.mime_type, 0)
                assert await media.get(120, 240) == (job.file_uri, job.mime_type, 0)
                upload.assert_awaited_once()
                await media.close()
            path.write_bytes(b'changed original')
            with pytest.raises(ValueError, match='changed'):
                await media.get(0, 120)
        asyncio.run(use())


def test_proxy_preparation_is_reported_before_encoding(asset, tmp_path):
    path = tmp_path / 'original.mov'; path.write_bytes(b'original')
    job = update_job(asset.job_id, local_path=str(path), technical={'duration_seconds': 60})
    async def encode(source, proxy, start, end):
        progress = get_job(job.job_id)
        assert progress.analysis_progress['stage'] == 'preparing_proxy'
        assert 'Preparing analysis proxy' in progress.progress
        assert progress.analysis_progress['upload_part'] == 1
        proxy.write_bytes(b'proxy')
    client = Mock()
    client.aio.files.upload = AsyncMock(return_value=SimpleNamespace(name='owned-proxy'))
    client.aio.files.delete = AsyncMock()
    async def use():
        with patch.object(video_segments, 'extract_segment', encode), \
             patch.object(video_segments, 'get_client', return_value=client), \
             patch.object(video_segments, 'poll_until_active', AsyncMock(return_value=SimpleNamespace(uri='proxy-uri'))):
            media = video_segments.SegmentedVideo(job)
            assert await media.get(0, 30) == ('proxy-uri', 'video/mp4', 0)
            await media.close()
    asyncio.run(use())
    assert path.read_bytes() == b'original'


@pytest.mark.parametrize('mode', ['auto', 'always', 'off'])
def test_api_forwards_validated_mode_to_worker(client, asset, tmp_path, mode):
    path = tmp_path / 'original.mov'; path.write_bytes(b'original')
    update_job(asset.job_id, local_path=str(path))
    with patch('app.routers.library.start_job') as start:
        result = client.post(f'/api/library/{asset.job_id}/analyze',
                             json={'proxy_mode': mode}, headers={'X-API-Key': 'test'})
        assert result.status_code == 202
        assert start.call_args.args[1].proxy_mode == mode


def test_off_rough_cut_worker_bypasses_proxy_and_keeps_later_window_times(asset, tmp_path):
    from test_rough_cut import provider
    from app.services.job_store import claim_rough_cut
    path = tmp_path / 'original.mov'; path.write_bytes(b'original')
    job = update_job(asset.job_id, local_path=str(path),
                     technical={'duration_seconds': 240, 'has_audio': True, 'codec': 'prores'})
    draft = rough_cut.new_draft(job, RoughCutRequest(proxy_mode='off'))
    assert claim_rough_cut(job.job_id, draft)
    try:
        with patch.object(rough_cut, 'get_client', return_value=provider()), \
             patch.object(rough_cut, 'ensure_remote_file', AsyncMock(return_value=job)) as upload, \
             patch.object(rough_cut, 'CutMedia') as proxy:
            asyncio.run(rough_cut.build(job.job_id, None))
            proxy.assert_not_called()
            upload.assert_awaited_once()
    finally:
        rough_cut._gate = None
    saved = get_job(job.job_id).rough_cut
    assert saved['status'] == 'ready', saved
    assert saved['proxy_mode'] == 'off' and not saved['proxy_used']
    assert saved['clips'][1]['start_seconds'] == 122
    assert path.read_bytes() == b'original'
