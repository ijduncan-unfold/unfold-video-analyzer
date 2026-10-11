import asyncio
import io
import json
import struct
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.services import camera_media, native_camera, sony_raw_worker as worker
from app.services.job_store import list_jobs
from app.services.media_cleanup import cleanup

FACTS = {'duration_seconds': 10., 'frame_rate': 24., 'frame_rate_fraction': '24/1',
         'width': 4096, 'height': 2160, 'codec': 'F55RAWSQ_4096_2160', 'has_audio': True,
         'source_timecode': '01:00:00:00', 'audio_stream_count': 4}


def picture(index=0, width=4128, height=2192):
    return b'SBRa' + struct.pack('>IHH', index, width, height//2) + b'\x00'*8


def klv(payload, picture_key=True):
    key = bytes.fromhex('060e2b34010201060e067f0315017f00') if picture_key else bytes.fromhex('060e2b34010101020301021001000000')
    return key + b'\x83' + len(payload).to_bytes(3, 'big') + payload


def test_mxf_walk_uses_actual_klv_lengths_not_fixture_stride():
    frames = [picture(0), picture(1)+b'variable bytes', picture(2)]
    data = klv(b'SBRa in non-picture metadata', False) + klv(frames[0]) + klv(b'pad', False) + klv(frames[1]) + klv(frames[2])
    assert list(worker.pictures(io.BytesIO(data), len(data))) == frames


@pytest.mark.parametrize('data', [b'bad header', klv(picture())[:-1], bytes.fromhex('060e2b34010201060e067f0315017f00') + b'\x80'])
def test_malformed_mxf_stops_without_resyncing_or_decoding(data):
    with pytest.raises(ValueError):
        list(worker.pictures(io.BytesIO(data), len(data)))


def test_picture_geometry_is_bounded_and_not_guessed():
    assert worker.picture_header(picture(7)) == (7, 4128, 2192)
    with pytest.raises(ValueError):
        worker.picture_header(picture(width=65534, height=65534))
    with pytest.raises(ValueError):
        worker.picture_header(picture(width=3))


@pytest.mark.parametrize('indices,expected_frames,error', [
    ([0, 1, 2], 3, None),
    ([2720, 2721, 2722], 3, None),
    ([2720, 2722, 2723], 3, 'sequence'),
    ([2720, 2721, 2721], 3, 'sequence'),
    ([2720, 2719, 2722], 3, 'sequence'),
    ([2720, 2721], 3, 'incomplete'),
    ([2720, 2721, 2722], 2, 'sequence'),
])
def test_complete_clip_may_start_above_zero_but_missing_or_reordered_frames_fail(tmp_path, indices, expected_frames, error):
    original = tmp_path / 'original.mxf'
    contents = b''.join(klv(picture(index)) for index in indices)
    original.write_bytes(contents)
    target = tmp_path / 'viewing.mp4'
    progress = tmp_path / 'progress.json'
    args = SimpleNamespace(source=str(original), target=str(target), progress=str(progress), fps='24000/1001',
        frames=expected_frames, width=4096, height=2160, black=512, white=5472, dll=str(original),
        shared=str(tmp_path), ffmpeg='ffmpeg')
    decoder = Mock()
    decoder.decode.return_value = b'pixels'
    processes = []
    def encode(command, **kwargs):
        output = Path(command[-1])
        output.write_bytes(b'encoded')
        process = Mock(stdin=io.BytesIO())
        process.wait.return_value = 0
        process.poll.return_value = 0
        processes.append(command)
        return process
    # Native decoding and process launch are simulated, including the Windows
    # flag that is absent from subprocess on Linux and macOS test runners.
    with patch.object(worker, 'Decoder', return_value=decoder), \
         patch.object(worker.subprocess, 'CREATE_NO_WINDOW', 0x08000000, create=True), \
         patch.object(worker.subprocess, 'Popen', side_effect=encode):
        if error:
            with pytest.raises(ValueError, match=error):
                worker.run(args)
            assert not target.exists()
        else:
            worker.run(args)
            assert target.exists()
            state = json.loads(progress.read_text())
            assert state['frames'] == expected_frames and state['stage'] == 'complete'
            # A nonzero picture counter never trims, offsets, or retimes proxy audio.
            assert processes[1][processes[1].index('-t') + 1] == str(expected_frames * 1001 / 24000)
        decoder.close.assert_called_once()
    assert original.read_bytes() == contents


def test_coded_plane_strides_and_active_crop_preserve_image_and_bgr_channels():
    decoder = worker.Decoder.__new__(worker.Decoder)
    decoder.dev, decoder.output = None, worker.RawBuffer()
    decoder.develop = lambda *args: 0
    decoder.r, decoder.b = np.full((6, 8), 1000, dtype=np.uint16), np.full((6, 8), 4000, dtype=np.uint16)
    decoder.g = np.full((6, 16), 2000, dtype=np.uint16)
    decoder.mosaic = np.empty((12, 16), dtype=np.uint16)
    decoder.crop = (2, 2, 12, 8)
    lut = (np.arange(65536) // 256).astype(np.uint8)
    image = np.frombuffer(decoder.decode(picture(), (12, 8), lut), dtype=np.uint8).reshape(8, 12, 3)
    assert (image == [15, 7, 3]).all()  # BGR, not RGB, and no diagonal stride corruption.


def test_viewing_transform_has_fixed_levels_for_the_entire_clip():
    lut = worker.viewing_lut(512, 5472)
    assert lut[0] == lut[512] == 0 and lut[5472] == lut[65535] == 255
    assert (np.diff(lut.astype(int)) >= 0).all()
    with pytest.raises(ValueError):
        worker.viewing_lut(512, 511)


def test_only_f55_mxf_routes_to_the_experimental_native_adapter():
    assert native_camera.supports_sony_f55('camera.mxf', FACTS)
    assert not native_camera.supports_sony_f55('camera.mxf', {**FACTS, 'codec':'XOCN'})
    assert not native_camera.supports_sony_f55('camera.braw', FACTS)


def test_unverified_dll_is_rejected_before_any_native_call(tmp_path):
    dll = tmp_path/'SonyRawDev.dll'
    dll.write_bytes(b'wrong version')
    if worker.sys.platform == 'win32':
        with pytest.raises(ValueError, match='version'):
            worker.Decoder(dll, tmp_path, picture(), 4096, 2160)


def test_f55_import_automatically_generates_viewing_copy_and_reuses_it(client, tmp_path):
    original = tmp_path/'sony.mxf'
    original.write_bytes(b'camera original')
    progress = []
    async def develop(source, folder, technical, on_progress):
        assert source == original.resolve() and technical['codec'] == FACTS['codec']
        folder.mkdir(parents=True)
        proxy = folder/'sony-viewing.mp4'
        proxy.write_bytes(b'generated video')
        on_progress({'message':'Preparing Sony RAW proxy', 'percent':50})
        return proxy, {'generated':True, 'codec':'h264', 'decoder':'sony_f55_raw', 'color_calibrated':False}
    with patch.object(camera_media, 'probe_media', AsyncMock(return_value=FACTS)), \
         patch.object(camera_media, 'decode_check', AsyncMock(return_value=False)), \
         patch.object(camera_media, 'create_poster', AsyncMock()) as poster, \
         patch.object(native_camera, 'prepare_sony_viewing', AsyncMock(side_effect=develop)) as convert:
        job = asyncio.run(camera_media.register_camera_source(str(original), on_progress=progress.append))
        repeated = asyncio.run(camera_media.register_camera_source(str(original)))
    assert len(list_jobs()) == 1 and repeated.job_id == job.job_id
    convert.assert_awaited_once()
    assert poster.call_args.args[0] == job.viewing_path
    assert job.local_path == str(original.resolve()) and job.size_bytes == len(b'camera original')
    assert job.technical['codec'] == FACTS['codec'] and job.technical['viewing_proxy']['generated']
    assert any(state.get('percent') == 50 for state in progress) and 'approximate' in job.warnings[0]
    assert progress[-1]['stage'] == 'finishing'
    assert cleanup(job) and not Path(job.viewing_path).exists()
    assert original.read_bytes() == b'camera original'


def test_failed_native_import_creates_no_project_and_keeps_original(client, tmp_path):
    source = tmp_path/'sony.mxf'; source.write_bytes(b'original')
    with patch.object(camera_media, 'probe_media', AsyncMock(return_value=FACTS)), \
         patch.object(camera_media, 'decode_check', AsyncMock(return_value=False)), \
         patch.object(native_camera, 'prepare_sony_viewing', AsyncMock(side_effect=ValueError('Unsupported bitstream'))):
        with pytest.raises(HTTPException, match='Unsupported bitstream'):
            asyncio.run(camera_media.register_camera_source(str(source)))
    assert not list_jobs() and source.read_bytes() == b'original'


def test_proxy_corruption_is_caught_before_repeat_import(client, tmp_path):
    source = tmp_path/'sony.mxf'; source.write_bytes(b'original')
    async def develop(original, folder, *args):
        folder.mkdir(parents=True)
        proxy=folder/'sony-viewing.mp4';proxy.write_bytes(b'proxy')
        return proxy, {'generated':True,'codec':'h264'}
    with patch.object(camera_media,'probe_media',AsyncMock(return_value=FACTS)), \
         patch.object(camera_media,'decode_check',AsyncMock(return_value=False)), \
         patch.object(camera_media,'create_poster',AsyncMock()), \
         patch.object(native_camera,'prepare_sony_viewing',AsyncMock(side_effect=develop)):
        job=asyncio.run(camera_media.register_camera_source(str(source)))
        Path(job.viewing_path).write_bytes(b'changed')
        with pytest.raises(HTTPException, match='changed'):
            asyncio.run(camera_media.register_camera_source(str(source)))


def test_import_progress_is_loopback_same_origin_and_contains_no_media_path(client, tmp_path):
    from app.routers import local_media
    ident=uuid.uuid4()
    local_media._import_progress[str(ident)]={'message':'Preparing Sony RAW proxy','stage':'decoding','percent':27,'updated_at':0}
    with TestClient(app,base_url='http://127.0.0.1:8000',client=('127.0.0.1',50000)) as local:
        response=local.get(f'/api/local-media/import-progress/{ident}')
        assert response.status_code==200 and response.json()['percent']==27
        assert 'updated_at' not in response.json()
        assert local.get(f'/api/local-media/import-progress/{ident}',headers={'Origin':'https://evil.invalid'}).status_code==403
    with TestClient(app,base_url='http://127.0.0.1:8000',client=('192.0.2.1',50000)) as remote:
        assert remote.get(f'/api/local-media/import-progress/{ident}').status_code==403
    local_media._import_progress.pop(str(ident))


def test_interpreted_frame_rate_rejected_before_native_decode(client,tmp_path):
    source=tmp_path/'sony.mxf';source.write_bytes(b'original')
    with patch.object(camera_media,'probe_media',AsyncMock(return_value=FACTS)), \
         patch.object(camera_media,'decode_check',AsyncMock(return_value=False)), \
         patch.object(native_camera,'prepare_sony_viewing',AsyncMock()) as decoder:
        with pytest.raises(HTTPException,match='interpreted'):
            asyncio.run(camera_media.register_camera_source(str(source),source_fps=25))
        decoder.assert_not_awaited()


@pytest.mark.parametrize('changed', [
    {'frame_rate_verified_frames': 239}, {'audio_stream_count': 3},
    {'duration_seconds': 9}, {'frame_rate_fraction': '25/1'},
    {'codec': 'hevc'}, {'has_audio': False},
])
def test_incomplete_or_misaligned_worker_output_is_removed_before_publication(client, tmp_path, changed):
    source = tmp_path/'original.mxf'; source.write_bytes(b'original')
    folder = Path(camera_media.settings.upload_dir)/str(uuid.uuid4()); folder.mkdir(parents=True)
    target = folder/'sony-viewing.mp4'; target.write_bytes(b'partial or bad proxy')
    (folder/'sony-progress.json').write_text(json.dumps({'stage':'complete','frames':240}))
    proc = SimpleNamespace(returncode=0, wait=AsyncMock(return_value=0))
    facts = {**FACTS, 'codec':'h264','width':1280,'height':676,'frame_rate_verified_frames':240, **changed}
    with patch.object(native_camera,'sony_runtime',return_value=(tmp_path/'SonyRawDev.dll',tmp_path)), \
         patch.object(native_camera.shutil,'which',return_value='ffmpeg'), \
         patch.object(native_camera.asyncio,'create_subprocess_exec',AsyncMock(return_value=proc)), \
         patch.object(native_camera,'probe_media',AsyncMock(return_value=facts)):
        with pytest.raises(ValueError,match='checks'):
            asyncio.run(native_camera._prepare_sony_viewing(source,folder,FACTS,None))
    assert not target.exists() and not (folder/'sony-progress.json').exists()
    assert source.read_bytes()==b'original'


def test_frozen_worker_dispatch_never_starts_the_desktop_server_or_window():
    from app import desktop
    with patch.object(worker,'main',return_value=7) as run, \
         patch.object(desktop,'configure_environment') as configure:
        assert desktop.main(['--sony-raw-worker','--source','camera.mxf'])==7
        run.assert_called_once_with(['--source','camera.mxf'])
        configure.assert_not_called()


def test_gemini_upload_uses_generated_viewing_proxy_and_mp4_mime(asset,tmp_path):
    from app.services import file_manager
    from app.services.job_store import update_job
    source=tmp_path/'original.mxf';source.write_bytes(b'original')
    proxy=tmp_path/'sony-viewing.mp4';proxy.write_bytes(b'proxy')
    original_fp,_,_=camera_media.source_fingerprint(source)
    proxy_fp,_,_=camera_media.source_fingerprint(proxy)
    job=update_job(asset.job_id,local_path=str(source),viewing_path=str(proxy),
                   media_origin='external',source_fingerprint=original_fp,viewing_fingerprint=proxy_fp,
                   file_id='',file_uri='',mime_type='application/mxf',
                   technical={**FACTS,'viewing_proxy':{'generated':True}})
    with patch.object(file_manager,'get_client'), \
         patch.object(file_manager,'upload_video',AsyncMock(return_value=SimpleNamespace(name='owned-proxy'))) as upload, \
         patch.object(file_manager,'poll_until_active',AsyncMock(return_value=SimpleNamespace(name='owned-proxy',uri='proxy-uri'))):
        result=asyncio.run(file_manager.ensure_remote_file(job))
        upload.assert_awaited_once_with(str(proxy),'video/mp4',api_key=None)
        assert result.file_uri=='proxy-uri'
    assert source.read_bytes()==b'original'

def test_worker_failure_keeps_bounded_diagnostics_and_public_phase(tmp_path):
    progress = tmp_path / 'sony-progress.json'
    target = tmp_path / 'sony-viewing.mp4'
    log = tmp_path / 'sony-viewing.ffmpeg.log'
    log.write_text('x' * 10000 + 'encoder ended unexpectedly')
    def fail(args):
        worker.emit(progress, stage='decoding', frames=73)
        raise BrokenPipeError('private source path should stay in diagnostics')
    arguments = ['--source', 'camera.mxf', '--target', str(target), '--progress', str(progress),
                 '--dll', 'decoder.dll', '--shared', 'shared', '--ffmpeg', 'ffmpeg',
                 '--fps', '24/1', '--width', '4096', '--height', '2160', '--frames', '240']
    with patch.object(worker, 'run', side_effect=fail):
        assert worker.main(arguments) == 1
    public = json.loads(progress.read_text())
    assert public['phase'] == 'decoding' and public['frames'] == 73
    assert public['error_type'] == 'BrokenPipeError' and not public['retryable']
    assert 'private source path' not in public['message']
    diagnostic = json.loads((tmp_path / 'sony-worker-error.json').read_text())
    assert 'private source path' in diagnostic['exception']
    assert len(diagnostic['encoder_log']) <= 8000
    assert diagnostic['encoder_log'].endswith('encoder ended unexpectedly')


def test_worker_only_marks_known_network_failures_retryable(tmp_path):
    import errno
    progress = tmp_path / 'progress.json'
    assert worker.failure_report(ConnectionResetError(errno.ECONNRESET, 'network disconnected'), progress)['retryable']
    assert not worker.failure_report(PermissionError(errno.EACCES, 'access denied'), progress)['retryable']
    assert not worker.failure_report(BrokenPipeError(errno.EPIPE, 'encoder pipe closed'), progress)['retryable']
    assert not worker.failure_report(ValueError('Invalid MXF payload'), progress)['retryable']


@pytest.mark.parametrize('original,retryable,expected_attempts', [
    ('\\\\server\\share\\clip.mxf', True, 2),
    ('\\\\server\\share\\clip.mxf', False, 1),
    ('C:/Camera/clip.mxf', True, 1),
])
def test_sony_retry_is_single_and_only_for_network_media(original, retryable, expected_attempts):
    error = native_camera.SonyWorkerError('interrupted', retryable=retryable)
    adapter = AsyncMock(side_effect=error)
    with patch.object(native_camera, '_gate', None), \
         patch.object(camera_media, 'source_fingerprint', return_value=('unchanged', original, 100)), \
         patch('app.services.editor_bridge.source_fingerprint', return_value=('unchanged', original, 100)), \
         patch.object(native_camera, '_prepare_sony_viewing', adapter), \
         patch.object(native_camera.asyncio, 'sleep', AsyncMock()):
        with pytest.raises(native_camera.SonyWorkerError):
            asyncio.run(native_camera.prepare_sony_viewing(original, Path('cache'), FACTS))
    assert adapter.await_count == expected_attempts


def test_sony_network_retry_reuses_only_an_unchanged_original():
    original = '\\\\server\\share\\clip.mxf'
    error = native_camera.SonyWorkerError('interrupted', retryable=True)
    progress = Mock()
    with patch.object(native_camera, '_gate', None), \
         patch('app.services.editor_bridge.source_fingerprint', side_effect=[('same', original, 100), ('same', original, 100)]), \
         patch.object(native_camera, '_prepare_sony_viewing', AsyncMock(side_effect=[error, ('proxy.mp4', FACTS)])) as adapter, \
         patch.object(native_camera.asyncio, 'sleep', AsyncMock()):
        assert asyncio.run(native_camera.prepare_sony_viewing(original, Path('cache'), FACTS, progress)) == ('proxy.mp4', FACTS)
        assert adapter.await_count == 2
        assert progress.call_args.args[0]['stage'] == 'retrying'
    with patch.object(native_camera, '_gate', None), \
         patch('app.services.editor_bridge.source_fingerprint', side_effect=[('before', original, 100), ('after', original, 100)]), \
         patch.object(native_camera, '_prepare_sony_viewing', AsyncMock(side_effect=error)) as adapter:
        with pytest.raises(ValueError, match='changed'):
            asyncio.run(native_camera.prepare_sony_viewing(original, Path('cache'), FACTS))
        assert adapter.await_count == 1


def test_native_worker_crash_retains_exit_and_last_frame(client, tmp_path):
    source = tmp_path / 'source.mxf'; source.write_bytes(b'original')
    folder = Path(camera_media.settings.upload_dir) / str(uuid.uuid4()); folder.mkdir(parents=True)
    (folder / 'sony-progress.json').write_text(json.dumps({'stage': 'decoding', 'frames': 74, 'message': 'Preparing Sony RAW proxy'}))
    proc = SimpleNamespace(returncode=0xc0000005, wait=AsyncMock(return_value=0xc0000005))
    with patch.object(native_camera, 'sony_runtime', return_value=(tmp_path/'SonyRawDev.dll', tmp_path)), \
         patch.object(native_camera.shutil, 'which', return_value='ffmpeg'), \
         patch.object(native_camera.asyncio, 'create_subprocess_exec', AsyncMock(return_value=proc)):
        with pytest.raises(native_camera.SonyWorkerError, match='stopped during decoding at frame 74'):
            asyncio.run(native_camera._prepare_sony_viewing(source, folder, FACTS, None))
    diagnostic = json.loads((folder / 'sony-import-error.json').read_text())
    assert diagnostic['worker_exit'] == 0xc0000005 and diagnostic['frames'] == 74
    assert not (folder / 'sony-progress.json').exists()
    assert source.read_bytes() == b'original'
