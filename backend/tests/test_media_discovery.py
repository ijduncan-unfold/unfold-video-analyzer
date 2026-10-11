import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from app import desktop_window, native_picker, windows_folder_picker
from app.main import app
from app.services import media_discovery


def write(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'original camera media')
    return path


def test_multiple_nested_folders_and_loose_clips_filter_sidecars_and_deduplicate(tmp_path):
    first = write(tmp_path / 'Card A' / 'Clip' / 'C001' / 'C001.MXF')
    second = write(tmp_path / 'Card B' / 'PRIVATE' / 'AVCHD' / 'STREAM' / '00001.MTS')
    loose = write(tmp_path / 'interview.mov')
    for name in ['slate.jpg', 'C001M01.xml', 'metadata.json', 'sound.wav', 'notes.txt']:
        write(first.parent / name)
    before = {str(path): path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    with patch.object(Path, 'open', side_effect=AssertionError('Discovery must not read media bytes')):
        result = media_discovery.discover_media([str(first.parent.parent.parent), str(second.parent.parent.parent.parent), str(loose), str(first.parent), str(first)])
    assert result == {'paths': sorted(map(str, [first, second, loose]), key=str.casefold), 'warnings': []}
    assert before == {str(path): path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}


def test_red_parts_and_arri_image_frames_become_one_recording_each(tmp_path):
    red = [write(tmp_path / 'RED' / f'A001_{number:03}.R3D') for number in [3, 1, 2]]
    arri = [write(tmp_path / 'ARRI' / f'A001.{number:07}.ari') for number in [102, 100, 101]]
    other = write(tmp_path / 'ARRI' / 'B001.0000200.arx')
    result = media_discovery.discover_media([str(tmp_path), str(red[0]), str(arri[0])])
    assert result['paths'] == sorted(map(str, [red[1], arri[1], other]), key=str.casefold)


def test_no_video_missing_folder_and_relative_path_are_reported(tmp_path):
    write(tmp_path / 'only sidecars.xml')
    assert media_discovery.discover_media([str(tmp_path)]) == {'paths': [], 'warnings': []}
    result = media_discovery.discover_media([str(tmp_path / 'offline folder')])
    assert result['paths'] == [] and 'offline' in result['warnings'][0]
    with pytest.raises(ValueError, match='absolute'):
        media_discovery.discover_media(['relative/folder'])


def test_discovery_does_not_follow_linked_folders_or_cycles(tmp_path):
    root = tmp_path / 'Card'; root.mkdir()
    actual = write(root / 'original.mp4')
    outside = write(tmp_path / 'Outside' / 'other.mp4')
    try:
        (root / 'external').symlink_to(outside.parent, target_is_directory=True)
        (root / 'cycle').symlink_to(root, target_is_directory=True)
    except OSError:
        pytest.skip('Symlinks unavailable on this machine')
    assert media_discovery.discover_media([str(root)]) == {'paths': [str(actual)], 'warnings': []}


def test_scan_limits_fail_explicitly_instead_of_silently_truncating(tmp_path, monkeypatch):
    write(tmp_path / 'first.mp4'); write(tmp_path / 'second.mp4')
    monkeypatch.setattr(media_discovery, 'MAX_CLIPS', 1)
    with pytest.raises(ValueError, match='more than 1 clips'):
        media_discovery.discover_media([str(tmp_path)])
    monkeypatch.setattr(media_discovery, 'MAX_CLIPS', 100)
    monkeypatch.setattr(media_discovery, 'MAX_ENTRIES', 1)
    with pytest.raises(ValueError, match='too many files'):
        media_discovery.discover_media([str(tmp_path)])


def test_unreadable_folder_does_not_hide_other_clips(tmp_path):
    readable = write(tmp_path / 'clip.mp4')
    folder = tmp_path / 'Offline'; folder.mkdir()
    with patch.object(media_discovery.os, 'scandir', side_effect=PermissionError):
        result = media_discovery.discover_media([str(folder), str(readable)])
    assert result['paths'] == [str(readable)] and 'Could not read folder' in result['warnings'][0]


def test_folder_endpoints_keep_loopback_origin_and_input_validation(client, tmp_path):
    original = write(tmp_path / 'Card' / 'Clip' / 'clip.mxf')
    with TestClient(app, base_url='http://127.0.0.1:8000', client=('127.0.0.1', 50000)) as local, \
         patch.object(native_picker, 'choose_folders', return_value=[str(original.parent.parent)]) as picker:
        assert local.post('/api/local-media/choose-folders', json={}).json() == {'paths': [str(original.parent.parent)]}
        response = local.post('/api/local-media/discover', json={'paths': [str(original.parent.parent)]})
        assert response.status_code == 200 and response.json()['paths'] == [str(original)]
        for endpoint, body in [('choose-folders', {}), ('discover', {'paths': [str(tmp_path)]})]:
            assert local.post('/api/local-media/' + endpoint, json=body, headers={'Origin': 'https://remote.invalid'}).status_code == 403
        for paths in [[], ['relative'], ['bad\x00path'], ['x' * 4097], ['x'] * 1001]:
            assert local.post('/api/local-media/discover', json={'paths': paths}).status_code == 422
        picker.assert_called_once()
    with TestClient(app, base_url='http://127.0.0.1:8000', client=('192.0.2.1', 50000)) as remote:
        assert remote.post('/api/local-media/discover', json={'paths': [str(tmp_path)]}).status_code == 403
        assert remote.post('/api/local-media/choose-folders', json={}).status_code == 403


def test_native_drop_forwards_multiple_folders_and_clip_paths(tmp_path):
    folder = tmp_path / 'Card'; folder.mkdir()
    clip = write(tmp_path / 'loose.mov')
    api = desktop_window.NativeFiles('http://127.0.0.1:8000/')
    api._window = Mock(get_current_url=Mock(return_value='http://127.0.0.1:8000/'))
    api._drop({'dataTransfer': {'files': [{'pywebviewFullPath': str(folder)}, {'pywebviewFullPath': str(clip)}]}})
    import json
    assert json.dumps([str(folder), str(clip)]) in api._window.evaluate_js.call_args.args[0]
    api._window.get_current_url.return_value = 'https://remote.invalid'
    with pytest.raises(ValueError, match='local'):
        api.choose_folders()


def test_folder_picker_lock_cancel_and_macos_multiselect(monkeypatch):
    monkeypatch.setattr(native_picker, 'sys', SimpleNamespace(platform='darwin'))
    with patch.object(native_picker.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='/Volumes/Camera A/\n/Volumes/Camera B/\n')) as launch:
        assert native_picker.choose_folders() == ['/Volumes/Camera A/', '/Volumes/Camera B/']
        assert 'choose folder' in launch.call_args.args[0][-1] and 'multiple selections allowed' in launch.call_args.args[0][-1]
        native_picker._picker_lock.acquire()
        try:
            with pytest.raises(ValueError, match='already open'):
                native_picker.choose_folders()
        finally:
            native_picker._picker_lock.release()
        launch.return_value = SimpleNamespace(returncode=0, stdout='')
        assert native_picker.choose_folders() == []
        launch.return_value = SimpleNamespace(returncode=1, stdout='')
        with pytest.raises(OSError):
            native_picker.choose_folders()
        assert not native_picker._picker_lock.locked()


@pytest.mark.skipif(os.name != 'nt', reason='Windows COM dialog ABI')
def test_real_windows_dialog_multiselect_options_without_showing_ui(monkeypatch):
    # Construct the actual OS dialog and inspect its options, but cancel before
    # showing a window so CI never waits for interactive input.
    method = windows_folder_picker._method
    seen = []
    def intercept(pointer, index, result_type, *argument_types):
        if index == 3:
            def cancel(dialog, owner):
                import ctypes
                flags = windows_folder_picker.DWORD()
                assert method(dialog, 10, windows_folder_picker.HRESULT, ctypes.POINTER(windows_folder_picker.DWORD))(dialog, ctypes.byref(flags)) == 0
                seen.append(flags.value)
                return windows_folder_picker.HRESULT(0x800704C7).value
            return cancel
        return method(pointer, index, result_type, *argument_types)
    monkeypatch.setattr(windows_folder_picker, '_method', intercept)
    assert native_picker.choose_folders() == []
    assert seen[0] & windows_folder_picker.FOLDER_OPTIONS == windows_folder_picker.FOLDER_OPTIONS
