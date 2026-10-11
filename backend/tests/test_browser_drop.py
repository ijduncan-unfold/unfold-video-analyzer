import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import native_picker
from app.services import browser_drop, camera_media, native_camera


def descriptor(path):
    return {'name': path.name, 'size': path.stat().st_size,
            'last_modified': path.stat().st_mtime_ns // 1_000_000,
            'edge_sha256': browser_drop.edge_digest(path, path.stat().st_size)}


def known(path):
    return SimpleNamespace(media_origin='external', local_path=str(path), status='ready')


def resolve(path):
    data = descriptor(path)
    return browser_drop.resolve_drop(data['name'], data['size'], data['last_modified'], data['edge_sha256'])


def test_browser_drop_resolves_adjacent_sony_recording_without_copying(tmp_path):
    first = tmp_path / 'Clip' / 'C001' / 'C001.mxf'
    second = tmp_path / 'Clip' / 'C002' / 'C002.mxf'
    first.parent.mkdir(parents=True); second.parent.mkdir()
    first.write_bytes(b'first recording'); second.write_bytes(b'new original' * 18000)
    original = second.read_bytes()
    files_before = sorted(str(path) for path in tmp_path.rglob('*'))
    with patch.object(browser_drop, 'list_jobs', return_value=[known(first)]):
        assert resolve(second) == str(second.resolve())
    assert second.read_bytes() == original
    assert sorted(str(path) for path in tmp_path.rglob('*')) == files_before


@pytest.mark.parametrize('size', [10, 65536, 65537, 90000, 131073])
def test_edge_hash_matches_browser_slice_algorithm(tmp_path, size):
    path = tmp_path / 'clip.mp4'; data = bytes(index % 251 for index in range(size)); path.write_bytes(data)
    edges = data[:65536] + (data[max(65536, size - 65536):] if size > 65536 else b'')
    assert browser_drop.edge_digest(path, size) == hashlib.sha256(edges).hexdigest()


def test_unknown_wrong_contents_wrong_time_and_ambiguous_files_require_picker(tmp_path):
    original = tmp_path / 'a' / 'clip.mp4'; other = tmp_path / 'b' / 'clip.mp4'
    original.parent.mkdir(); other.parent.mkdir()
    original.write_bytes(b'original'); other.write_bytes(b'original')
    browser_drop.os.utime(other, ns=(original.stat().st_atime_ns, original.stat().st_mtime_ns))
    data = descriptor(original)
    with patch.object(browser_drop, 'list_jobs', return_value=[]):
        assert resolve(original) is None
    with patch.object(browser_drop, 'list_jobs', return_value=[known(original), known(other)]):
        assert resolve(original) is None
    with patch.object(browser_drop, 'list_jobs', return_value=[known(original)]):
        assert browser_drop.resolve_drop(data['name'], data['size'], data['last_modified'], '0' * 64) is None
        assert browser_drop.resolve_drop(data['name'], data['size'], data['last_modified'] + 50, data['edge_sha256']) is None


def test_drop_cannot_follow_symlink_outside_known_folder(tmp_path):
    root = tmp_path / 'known'; root.mkdir()
    outside = tmp_path / 'outside.mp4'; outside.write_bytes(b'outside original')
    try:
        (root / outside.name).symlink_to(outside)
    except OSError:
        pytest.skip('Symlink creation unavailable')
    with patch.object(browser_drop, 'list_jobs', return_value=[known(root / 'registered.mp4')]):
        assert resolve(outside) is None


def test_browser_drop_and_picker_endpoints_require_loopback_and_same_origin(client, tmp_path):
    path = tmp_path / 'clip.mp4'; path.write_bytes(b'original')
    with TestClient(app, base_url='http://127.0.0.1:8000', client=('127.0.0.1', 50000)) as local, \
         patch.object(browser_drop, 'list_jobs', return_value=[known(path)]), \
         patch.object(native_picker, 'choose_files', return_value=[str(path)]) as choose:
        response = local.post('/api/local-media/resolve-drop', json=descriptor(path))
        assert response.status_code == 200 and Path(response.json()['media_path']) == path
        assert local.post('/api/local-media/choose-files', json={}).json() == {'paths': [str(path)]}
        for endpoint, body in [('resolve-drop', descriptor(path)), ('choose-files', {})]:
            assert local.post('/api/local-media/' + endpoint, json=body, headers={'Origin': 'https://evil.invalid'}).status_code == 403
        for filename in ['../clip.mp4', 'C:\\clip.mp4', 'clip.mp4\x00', 'unknown.exe']:
            assert local.post('/api/local-media/resolve-drop', json={**descriptor(path), 'name': filename}).status_code == 422
        choose.assert_called_once()
    with TestClient(app, base_url='http://127.0.0.1:8000', client=('192.0.2.1', 50000)) as remote:
        assert remote.post('/api/local-media/choose-files', json={}).status_code == 403


def test_picker_busy_cancel_and_errors_do_not_start_import():
    with patch.object(native_picker.sys, 'platform', 'win32'), \
         patch.object(native_picker, '_windows_picker', return_value=[]) as choose:
        assert native_picker.choose_files() == []
        native_picker._picker_lock.acquire()
        try:
            with pytest.raises(ValueError, match='already open'):
                native_picker.choose_files()
        finally:
            native_picker._picker_lock.release()
        choose.assert_called_once()
        choose.side_effect = OSError('failure')
        with pytest.raises(OSError):
            native_picker.choose_files()
        assert not native_picker._picker_lock.locked()


@pytest.mark.skipif(native_picker.sys.platform != 'win32', reason='Windows common dialog ABI')
@pytest.mark.parametrize('selection', [r'\\server\share\camera.mov', 'C:\\Camera day\x00A one.mxf\x00B two.mxf\x00\x00'])
def test_windows_picker_parses_absolute_unc_and_multiple_paths_without_launching_app(selection):
    import ctypes

    def select(form_ptr):
        form = form_ptr._obj
        assert form.lStructSize == (152 if ctypes.sizeof(ctypes.c_void_p) == 8 else 88)
        assert form.Flags & 0x8  # Preserve the backend's current working directory.
        address = ctypes.c_void_p.from_address(ctypes.addressof(form) + type(form).lpstrFile.offset).value
        value = ctypes.create_unicode_buffer(selection)
        ctypes.memmove(address, value, ctypes.sizeof(value))
        return True

    dialog = SimpleNamespace(GetOpenFileNameW=Mock(side_effect=select), CommDlgExtendedError=Mock(return_value=0))
    with patch.object(native_picker.ctypes, 'WinDLL', return_value=dialog):
        paths = native_picker._windows_picker()
    expected = [selection] if '\x00' not in selection else ['C:\\Camera day\\A one.mxf', 'C:\\Camera day\\B two.mxf']
    assert paths == expected


def test_import_reports_queue_then_inspection_and_preview_stages(client, tmp_path):
    path = tmp_path / 'clip.mp4'; path.write_bytes(b'original')
    facts = {'duration_seconds': 2, 'frame_rate': 24, 'frame_rate_fraction': '24/1', 'codec': 'h264', 'width': 640, 'height': 360}

    async def scenario():
        # A new event-loop lock for this concurrency test, independent of other tests.
        with patch.object(camera_media, '_link_lock', asyncio.Lock()), \
             patch.object(camera_media, 'probe_media', AsyncMock(return_value=facts)), \
             patch.object(camera_media, 'decode_check', AsyncMock(return_value=True)), \
             patch.object(camera_media, 'create_poster', AsyncMock()):
            await camera_media._link_lock.acquire()
            states = []
            task = asyncio.create_task(camera_media.register_camera_source(str(path), on_progress=states.append))
            await asyncio.sleep(0)
            assert states[-1]['stage'] == 'queued'
            camera_media._link_lock.release()
            await task
            assert [s['stage'] for s in states] == ['queued', 'locating', 'inspecting', 'checking_decoder', 'finishing']
    asyncio.run(scenario())


def test_known_sony_raw_uses_native_decoder_without_failed_ffmpeg_attempt(client, tmp_path):
    path = tmp_path / 'clip.mxf'; path.write_bytes(b'original')
    facts = {'duration_seconds': 2, 'frame_rate': 24, 'frame_rate_fraction': '24/1', 'codec': 'F55RAWSQ_4096_2160', 'width': 4096, 'height': 2160}

    async def develop(source, folder, technical, on_progress):
        folder.mkdir(parents=True); proxy = folder / 'view.mp4'; proxy.write_bytes(b'generated proxy')
        return proxy, {'generated': True, 'decoder': 'sony_f55_raw', 'codec': 'h264'}

    with patch.object(camera_media, 'probe_media', AsyncMock(return_value=facts)), \
         patch.object(camera_media, 'decode_check', AsyncMock()) as ffmpeg, \
         patch.object(camera_media, 'create_poster', AsyncMock()), \
         patch.object(native_camera, 'prepare_sony_viewing', AsyncMock(side_effect=develop)) as decoder:
        job = asyncio.run(camera_media.register_camera_source(str(path)))
        ffmpeg.assert_not_awaited(); decoder.assert_awaited_once()
        assert job.local_path == str(path.resolve())
        assert path.read_bytes() == b'original'
