import json
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from app import desktop_window
from app.main import app


@pytest.mark.parametrize('url', ['https://remote.invalid/', 'file:///tmp/media', 'http://localhost@remote.invalid/', 'http://127.0.0.1/other', 'http://127.0.0.1/?redirect=external'])
def test_native_bridge_rejects_non_app_origins(url):
    with pytest.raises(ValueError):
        desktop_window.local_origin(url)


def test_drop_transfers_native_paths_only_and_never_reads_or_copies_media(tmp_path):
    original = tmp_path / "clip 'quoted'.mxf"
    original.write_bytes(b'original')
    api = desktop_window.NativeFiles('http://127.0.0.1:8000/')
    window = Mock()
    window.get_current_url.return_value = 'http://127.0.0.1:8000/'
    api._window = window
    api._drop({'dataTransfer': {'files': [{'name': original.name, 'pywebviewFullPath': str(original)}, {'pywebviewFullPath': str(original)}, {'name': 'missing-path.mp4'}, {'pywebviewFullPath': 'relative.mp4'}]}})
    script = window.evaluate_js.call_args.args[0]
    assert json.dumps([str(original)]) in script
    assert original.read_bytes() == b'original'
    assert 'read' not in script and 'upload' not in script


def test_native_bridge_preserves_unc_paths_on_windows():
    if os.name != 'nt':
        pytest.skip('UNC paths are Windows-native')
    path = r'\\server\share\Shoot day\camera.mxf'
    api = desktop_window.NativeFiles('http://127.0.0.1:8000/')
    api._window = Mock(get_current_url=Mock(return_value='http://127.0.0.1:8000/'))
    api._drop({'dataTransfer': {'files': [{'pywebviewFullPath': path}]}})
    assert json.dumps([path]) in api._window.evaluate_js.call_args.args[0]


def test_external_navigation_cannot_use_native_picker_or_receive_paths():
    api = desktop_window.NativeFiles('http://127.0.0.1:8000/')
    api._window = Mock(get_current_url=Mock(return_value='https://remote.invalid/'))
    with pytest.raises(ValueError):
        api.choose_files()
    api._drop({'dataTransfer': {'files': [{'pywebviewFullPath': str(Path.cwd() / 'clip.mp4')}]}})
    api._window.evaluate_js.assert_not_called()
    api._window.create_file_dialog.assert_not_called()


def test_native_picker_cancel_returns_no_files(monkeypatch):
    monkeypatch.setitem(desktop_window.sys.modules, 'webview', SimpleNamespace(FileDialog=SimpleNamespace(OPEN=10)))
    api = desktop_window.NativeFiles('http://127.0.0.1:8000/')
    api._window = Mock(get_current_url=Mock(return_value='http://127.0.0.1:8000/'), create_file_dialog=Mock(return_value=None))
    assert api.choose_files() == []
    assert api._window.create_file_dialog.call_args.kwargs['allow_multiple'] is True


def test_native_launcher_keeps_pairing_out_of_command_line_and_reuses_window(monkeypatch):
    monkeypatch.setattr(desktop_window, '_process', None)
    monkeypatch.setattr(desktop_window, 'ui_version', lambda: '0123456789abcdef')
    # The launcher contract uses a stub process; it does not require a GUI
    # package or display server on backend-only CI runners.
    monkeypatch.setattr(importlib.util, 'find_spec', Mock(return_value=object()))
    launch = Mock(return_value=Mock(poll=Mock(return_value=None)))
    monkeypatch.setattr(desktop_window.subprocess, 'Popen', launch)
    desktop_window.launch_window('https://localhost:8443', 'private-test-token')
    desktop_window.launch_window('https://localhost:8443', 'private-test-token')
    launch.assert_called_once()
    assert 'private-test-token' not in ' '.join(launch.call_args.args[0])
    assert launch.call_args.kwargs['env']['VIDEO_ANALYZER_WINDOW_URL'] == 'https://localhost:8443/?ui=0123456789abcdef#desktop-token=private-test-token'


def test_native_ui_version_changes_with_frontend_build_and_keeps_bridge_origin(tmp_path, monkeypatch):
    monkeypatch.setenv('VIDEO_ANALYZER_FRONTEND_DIR', str(tmp_path))
    page = tmp_path / 'index.html'
    page.write_text('<script src="/assets/old.js"></script>')
    previous = desktop_window.ui_version()
    page.write_text('<script src="/assets/new.js"></script>')
    current = desktop_window.ui_version()
    assert previous != current and len(current) == 16
    assert desktop_window.local_origin(f'http://127.0.0.1:8000/?ui={current}') == ('http', '127.0.0.1:8000')
    api = desktop_window.NativeFiles(f'http://127.0.0.1:8000/?ui={current}')
    api._window = Mock(get_current_url=Mock(return_value=f'http://127.0.0.1:8000/?ui={current}'))
    assert api._trusted()
    for query in ('ui=bad', 'ui=' + current + '&redirect=external', 'redirect=external'):
        with pytest.raises(ValueError):
            desktop_window.local_origin(f'http://127.0.0.1:8000/?{query}')


def test_native_launcher_without_desktop_dependencies_explains_the_requirement(monkeypatch):
    monkeypatch.setattr(desktop_window, '_process', None)
    monkeypatch.setattr(importlib.util, 'find_spec', Mock(return_value=None))
    launch = Mock()
    monkeypatch.setattr(desktop_window.subprocess, 'Popen', launch)
    with pytest.raises(ValueError, match='desktop dependencies'):
        desktop_window.launch_window('http://127.0.0.1:8000')
    launch.assert_not_called()


def test_browser_handoff_requires_local_origin_and_does_not_import_or_copy(client):
    with TestClient(app, base_url='http://127.0.0.1:8000', client=('127.0.0.1', 50000)) as local:
        with pytest.MonkeyPatch.context() as patch:
            launch = Mock()
            patch.setattr(desktop_window, 'launch_window', launch)
            patch.delenv('VIDEO_ANALYZER_DESKTOP', raising=False)
            assert local.post('/api/local-media/open-desktop', json={'pick': True}, headers={'Origin':'https://remote.invalid'}).status_code == 403
            launch.assert_not_called()
            assert local.post('/api/local-media/open-desktop', json={'pick': True}).status_code == 200
            launch.assert_called_once_with('http://127.0.0.1:8000', '', True)
