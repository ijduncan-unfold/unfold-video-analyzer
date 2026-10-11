"""Portable desktop regressions; macOS commands are mocked, never executed."""
import importlib.util
import json
import logging
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import shutil
import socket
import ssl
import threading
import urllib.error
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app import desktop
from app.desktop_tls import LocalTLS, TLSSetupRequired, configured_tls, smoke_tls
from app.config import settings
from app.services import editor_bridge


@pytest.fixture(autouse=True)
def restore_desktop_environment():
    # configure_environment writes directly to os.environ, including keys that
    # were absent. Restore those writes as well as individually monkeypatched keys.
    names = ('VIDEO_ANALYZER_DESKTOP', 'DATABASE_PATH', 'UPLOAD_DIR', 'EDITOR_TOKEN_PATH',
             'EDITOR_DISCOVERY_PATH', 'VIDEO_ANALYZER_FRONTEND_DIR', 'PATH')
    original = {name: os.environ.get(name) for name in names}
    yield
    for name, value in original.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


@pytest.mark.parametrize('platform, suffix', [
    ('darwin', ('Library', 'Application Support', 'VideoAnalyzer')),
    ('win32', ('AppData', 'Local', 'VideoAnalyzer')),
    ('linux', ('.local', 'share', 'video-analyzer')),
])
def test_default_data_directory_is_in_user_space(monkeypatch, tmp_path, platform, suffix):
    monkeypatch.setattr(desktop.sys, 'platform', platform)
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.delenv('LOCALAPPDATA', raising=False)
    monkeypatch.delenv('XDG_DATA_HOME', raising=False)
    assert desktop.default_data_dir() == tmp_path.joinpath(*suffix)


def test_mac_ignores_windows_and_linux_data_directory_overrides(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop.sys, 'platform', 'darwin')
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'windows'))
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'linux'))
    assert desktop.default_data_dir() == tmp_path / 'Library' / 'Application Support' / 'VideoAnalyzer'


def test_mac_environment_uses_private_data_and_homebrew_paths_without_touching_media(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop.sys, 'platform', 'darwin')
    data = tmp_path / 'Application Support' / 'VideoAnalyzer'
    data.mkdir(parents=True)
    retained = {
        'library.sqlite3': b'existing-library',
        'desktop-settings.json': b'{"google_api_key":"fake-user-key"}',
        'premiere-token.txt': b'fake-user-pairing-token',
    }
    for name, content in retained.items():
        (data / name).write_bytes(content)
    source = tmp_path / 'Original film.mov'
    source.write_bytes(b'original-media')
    monkeypatch.setenv('PATH', 'existing-system-path')
    monkeypatch.setenv('DATABASE_PATH', 'wrong-developer-library')
    monkeypatch.setenv('UPLOAD_DIR', 'wrong-developer-media')
    monkeypatch.setenv('EDITOR_TOKEN_PATH', 'wrong-developer-token')
    desktop.configure_environment(data)
    assert desktop.os.environ['VIDEO_ANALYZER_DESKTOP'] == '1'
    assert desktop.os.environ['DATABASE_PATH'] == str(data / 'library.sqlite3')
    assert desktop.os.environ['UPLOAD_DIR'] == str(data / 'media')
    assert desktop.os.environ['EDITOR_TOKEN_PATH'] == str(data / 'premiere-token.txt')
    assert desktop.os.environ['PATH'].split(desktop.os.pathsep) == [
        str(Path('/opt/homebrew/bin')), str(Path('/usr/local/bin')), 'existing-system-path',
    ]
    for name, content in retained.items():
        assert (data / name).read_bytes() == content
    assert source.read_bytes() == b'original-media'
    assert not list(data.glob('*.mov'))


def test_frozen_frontend_path_uses_bundle_while_database_stays_in_user_data(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop.sys, 'platform', 'darwin')
    monkeypatch.setattr(desktop.sys, 'frozen', True, raising=False)
    bundle = tmp_path / 'bundle-resources'
    monkeypatch.setattr(desktop.sys, '_MEIPASS', str(bundle), raising=False)
    data = tmp_path / 'user-data'
    desktop.configure_environment(data)
    assert desktop.os.environ['VIDEO_ANALYZER_FRONTEND_DIR'] == str(bundle / 'frontend' / 'dist')
    assert desktop.os.environ['DATABASE_PATH'] == str(data / 'library.sqlite3')
    assert not (bundle / 'desktop-settings.json').exists()


def test_custom_data_directory_isolates_discovery_from_regular_companion(monkeypatch, tmp_path):
    regular = tmp_path / 'regular-data'
    regular.mkdir()
    active = regular / 'editor-service.json'
    active.write_bytes(b'active-regular-companion-descriptor')
    monkeypatch.setattr(desktop, 'default_data_dir', Mock(return_value=regular))
    monkeypatch.setenv('EDITOR_DISCOVERY_PATH', str(active))
    custom = tmp_path / 'qa-data'
    desktop.configure_environment(custom)
    assert os.environ['EDITOR_DISCOVERY_PATH'] == str(custom / 'editor-service.json')
    assert active.read_bytes() == b'active-regular-companion-descriptor'


def test_smoke_isolates_discovery_and_declares_verified_https_before_worker_starts(monkeypatch, tmp_path):
    import uvicorn
    from app.main import app

    regular = tmp_path / 'regular-data'
    regular.mkdir()
    active = regular / 'editor-service.json'
    active.write_bytes(b'active-regular-companion-descriptor')
    monkeypatch.setattr(desktop.sys, 'platform', 'darwin')
    monkeypatch.setattr(desktop, 'default_data_dir', Mock(return_value=regular))
    monkeypatch.setenv('EDITOR_DISCOVERY_PATH', str(active))
    monkeypatch.setattr(desktop, 'RotatingFileHandler', Mock())
    monkeypatch.setattr(desktop, 'logging', SimpleNamespace(
        INFO=logging.INFO, Formatter=logging.Formatter, basicConfig=Mock(), getLogger=Mock(return_value=Mock()),
    ))
    context = ssl.create_default_context()
    tls = Mock()
    tls.server_options.return_value = {}
    tls.client_context.return_value = context
    monkeypatch.setattr(desktop, 'smoke_tls', Mock(return_value=tls))
    monkeypatch.setattr(desktop.socket, 'socket', Mock(return_value=Mock()))
    monkeypatch.setattr(editor_bridge, 'get_pairing_token', Mock(return_value='t' * 43))
    server = SimpleNamespace(started=False, should_exit=False, run=Mock())
    monkeypatch.setattr(uvicorn, 'Server', Mock(return_value=server))
    monkeypatch.setattr(uvicorn, 'Config', Mock())
    monkeypatch.setattr(app.state, 'editor_connection_server', 'previous', raising=False)
    monkeypatch.setattr(app.state, 'editor_connection_ssl_context', None, raising=False)
    isolated = []

    def started():
        assert app.state.editor_connection_server == 'https://localhost:8443'
        assert app.state.editor_connection_ssl_context is context
        isolated.append(Path(os.environ['EDITOR_DISCOVERY_PATH']))
        assert isolated[0] != active and isolated[0].parent != regular
        assert active.read_bytes() == b'active-regular-companion-descriptor'

    worker = Mock()
    worker.start.side_effect = started
    worker.is_alive.return_value = False  # Stop before any actual server/network work.
    monkeypatch.setattr(desktop.threading, 'Thread', Mock(return_value=worker))
    assert desktop.main(['--data-dir', str(regular), '--smoke-test', '--no-browser', '--no-tray']) == 1
    assert len(isolated) == 1
    assert not isolated[0].parent.exists()
    assert active.read_bytes() == b'active-regular-companion-descriptor'


def test_installed_settings_read_only_private_user_config_and_disable_dotenv(monkeypatch, tmp_path):
    data = tmp_path / 'private-user-data'
    data.mkdir()
    key = 'fake-private-user-key'
    config = data / 'desktop-settings.json'
    content = json.dumps({'google_api_key': key})
    config.write_text(content, encoding='utf-8')
    monkeypatch.setenv('VIDEO_ANALYZER_DESKTOP', '1')
    monkeypatch.setenv('DATABASE_PATH', str(data / 'library.sqlite3'))
    monkeypatch.setenv('GOOGLE_API_KEY', '')
    spec = importlib.util.spec_from_file_location('desktop_config_test', Path(desktop.__file__).with_name('config.py'))
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(desktop.sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    assert module.Settings.model_config['env_file'] == ()
    assert module.settings.google_api_key == key
    assert config.read_text(encoding='utf-8') == content


def test_mac_dialog_passes_text_as_an_argument_not_interpolated_applescript(monkeypatch):
    monkeypatch.setattr(desktop.sys, 'platform', 'darwin')
    run = Mock()
    monkeypatch.setattr(desktop.subprocess, 'run', run)
    message = 'Film "Cámara A"\\Take 1\nKeep the original file.'
    desktop.show_message(message, error=True)
    args = run.call_args.args[0]
    assert args[:2] == ['/usr/bin/osascript', '-e']
    assert 'item 1 of argv' in args[2]
    assert message not in args[2]
    assert args[3] == message
    assert run.call_args.kwargs == {'check': False}


def test_mac_pairing_opens_only_the_private_token_in_textedit(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop.sys, 'platform', 'darwin')
    token_path = tmp_path / 'Application Support' / 'premiere-token.txt'
    monkeypatch.setattr(settings, 'editor_token_path', str(token_path))
    get_token = Mock(return_value='fake-user-requested-token')
    monkeypatch.setattr(editor_bridge, 'get_pairing_token', get_token)
    launch = Mock()
    monkeypatch.setattr(desktop.subprocess, 'Popen', launch)
    desktop.show_pairing_token()
    get_token.assert_called_once_with()
    launch.assert_called_once_with(['/usr/bin/open', '-a', 'TextEdit', str(token_path)])


def test_mac_data_folder_open_uses_argument_list(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop.sys, 'platform', 'darwin')
    launch = Mock()
    monkeypatch.setattr(desktop.subprocess, 'Popen', launch)
    folder = tmp_path / 'Application Support' / 'VideoAnalyzer'
    desktop._open_data_folder(folder)
    launch.assert_called_once_with(['/usr/bin/open', str(folder)])


def test_desktop_launch_passes_pairing_to_native_window_on_verified_https_origin(monkeypatch):
    token = 'p' * 43
    monkeypatch.setattr(editor_bridge, 'get_pairing_token', Mock(return_value=token))
    from app import desktop_window
    native = Mock()
    monkeypatch.setattr(desktop_window, 'launch_window', native)
    desktop._open_ui('https://localhost:8443')
    native.assert_called_once_with('https://localhost:8443', token)


@pytest.fixture
def isolated_tls(monkeypatch):
    """Real verified TLS on this test host; no mkcert, Keychain, or Mac binaries."""
    if not shutil.which('openssl'):
        git_openssl = Path('C:/Program Files/Git/usr/bin/openssl.exe')
        if not git_openssl.is_file():
            pytest.skip('OpenSSL is unavailable for the isolated TLS handshake test')
        monkeypatch.setenv('PATH', str(git_openssl.parent) + os.pathsep + os.environ.get('PATH', ''))
    with ExitStack() as resources:
        yield smoke_tls(resources)


def test_smoke_certificate_cleanup_never_changes_user_certificates(isolated_tls, tmp_path):
    user = tmp_path / 'user-data' / 'tls'
    user.mkdir(parents=True)
    retained = user / 'localhost-key.pem'
    retained.write_bytes(b'existing-user-private-key')
    assert isolated_tls.key.parent != user
    assert retained.read_bytes() == b'existing-user-private-key'
    assert isolated_tls.cert.is_file()
    assert isolated_tls.key.is_file()
    with ExitStack() as resources:
        ephemeral = smoke_tls(resources)
        temporary_directory = ephemeral.key.parent
        assert temporary_directory.is_dir()
    assert not temporary_directory.exists()
    assert retained.read_bytes() == b'existing-user-private-key'


def test_tls_client_context_requires_chain_and_hostname_verification(isolated_tls):
    context = isolated_tls.client_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
    options = isolated_tls.server_options()
    assert options['ssl_certfile'] == str(isolated_tls.cert)
    assert options['ssl_keyfile'] == str(isolated_tls.key)
    assert options['ssl_version'] == ssl.PROTOCOL_TLS_SERVER


def test_local_https_reads_with_its_public_ca_and_rejects_wrong_hostname_and_untrusted_ca(isolated_tls):
    class QuietHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            payload = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(str(isolated_tls.cert), str(isolated_tls.key))
    server.socket = server_context.wrap_socket(server.socket, server_side=True)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        port = server.server_address[1]
        verified = isolated_tls.client_context()
        for host in ('localhost', '127.0.0.1'):
            assert desktop._get_json(f'https://{host}:{port}/health', verified) == {'status': 'ok'}
        with pytest.raises(urllib.error.URLError) as refused:
            desktop._get_json(f'https://localhost:{port}/health', ssl.create_default_context())
        assert isinstance(refused.value.reason, ssl.SSLCertVerificationError)
        with socket.create_connection(('127.0.0.1', port), timeout=2) as connection:
            with pytest.raises(ssl.SSLCertVerificationError):
                verified.wrap_socket(connection, server_hostname='unrelated.example')
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


@pytest.mark.parametrize('supplied', [
    ('cert.pem', None, None), (None, 'key.pem', None), (None, None, 'ca.pem'),
    ('cert.pem', 'key.pem', None),
])
def test_partial_tls_overrides_are_rejected_before_files_or_sockets(tmp_path, supplied):
    with pytest.raises(ValueError, match='together'):
        configured_tls(tmp_path, *supplied)


def test_missing_or_invalid_tls_material_is_rejected_before_server_options(tmp_path):
    with pytest.raises(TLSSetupRequired, match='not been set up'):
        configured_tls(tmp_path)
    directory = tmp_path / 'tls'
    directory.mkdir()
    for filename in ('localhost.pem', 'localhost-key.pem', 'rootCA.pem'):
        (directory / filename).write_text('invalid PEM', encoding='utf-8')
    with pytest.raises(TLSSetupRequired, match='missing or invalid'):
        configured_tls(tmp_path)


def test_configured_tls_selects_default_user_files_and_explicit_overrides(monkeypatch, tmp_path):
    directory = tmp_path / 'tls'
    directory.mkdir()
    for name in ('localhost.pem', 'localhost-key.pem', 'rootCA.pem'):
        (directory / name).write_text('mock PEM', encoding='utf-8')
    validate = Mock(return_value={})
    monkeypatch.setattr(LocalTLS, 'server_options', validate)
    default = configured_tls(tmp_path)
    assert (default.cert, default.key, default.ca) == (
        directory / 'localhost.pem', directory / 'localhost-key.pem', directory / 'rootCA.pem',
    )
    override = configured_tls(tmp_path, default.cert, default.key, default.ca)
    assert override == default
    assert validate.call_count == 2


def test_mac_missing_tls_offers_explicit_setup_without_binding_a_port(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop.sys, 'platform', 'darwin')
    monkeypatch.setattr(desktop, 'RotatingFileHandler', Mock())
    # Replace this module's logger facade, preserving pytest's global logging.
    monkeypatch.setattr(desktop, 'logging', SimpleNamespace(
        INFO=logging.INFO, Formatter=logging.Formatter, basicConfig=Mock(), getLogger=Mock(return_value=Mock()),
    ))
    tls = Mock(side_effect=TLSSetupRequired('Explicit setup required'))
    monkeypatch.setattr(desktop, 'configured_tls', tls)
    create_socket = Mock(side_effect=AssertionError('TLS must validate before binding'))
    monkeypatch.setattr(desktop.socket, 'socket', create_socket)
    offer = Mock()
    monkeypatch.setattr(desktop, '_offer_mac_setup', offer)
    assert desktop.main(['--data-dir', str(tmp_path), '--no-browser', '--no-tray']) == 1
    tls.assert_called_once_with(tmp_path.resolve(), None, None, None)
    offer.assert_called_once_with('Explicit setup required', tmp_path.resolve())
    create_socket.assert_not_called()


@pytest.mark.parametrize('flags, expected_port, uses_tls', [
    ([], 8443, True), (['--http'], 8000, False), (['--https', '--port', '18742'], 18742, True),
])
def test_mac_default_https_port_and_explicit_http_optout(monkeypatch, tmp_path, flags, expected_port, uses_tls):
    monkeypatch.setattr(desktop.sys, 'platform', 'darwin')
    monkeypatch.setattr(desktop, 'RotatingFileHandler', Mock())
    monkeypatch.setattr(desktop, 'logging', SimpleNamespace(
        INFO=logging.INFO, Formatter=logging.Formatter, basicConfig=Mock(), getLogger=Mock(return_value=Mock()),
    ))
    tls = Mock()
    tls.server_options.return_value = {}
    select_tls = Mock(return_value=tls)
    monkeypatch.setattr(desktop, 'configured_tls', select_tls)
    listener = Mock()
    listener.bind.side_effect = OSError('Test occupied port; do not start a real engine')
    monkeypatch.setattr(desktop.socket, 'socket', Mock(return_value=listener))
    monkeypatch.setattr(desktop, 'show_message', Mock())
    assert desktop.main(['--data-dir', str(tmp_path), '--no-browser', '--no-tray', *flags]) == 1
    listener.bind.assert_called_once_with(('127.0.0.1', expected_port))
    assert select_tls.called is uses_tls
    listener.close.assert_called_once()


@pytest.mark.parametrize('extra', [
    ['--http', '--https'], ['--http', '--tls-cert', 'cert.pem'], ['--port', '1023'],
])
def test_invalid_transport_flags_are_rejected_before_configuration(monkeypatch, extra):
    configure = Mock()
    monkeypatch.setattr(desktop, 'configure_environment', configure)
    with pytest.raises(SystemExit) as refused:
        desktop.main(extra)
    assert refused.value.code == 2
    configure.assert_not_called()


def test_mac_setup_opens_terminal_with_quoted_arguments_after_explicit_action(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop.sys, 'platform', 'darwin')
    helper = tmp_path / 'Setup Premiere.command'
    helper.write_text('# test placeholder', encoding='utf-8')
    monkeypatch.setattr(desktop, '_resource_path', Mock(return_value=helper))
    launch = Mock()
    monkeypatch.setattr(desktop.subprocess, 'Popen', launch)
    data = tmp_path / 'Application Support' / 'VideoAnalyzer'
    desktop._setup_premiere_mac(data)
    args = launch.call_args.args[0]
    assert args[:2] == ['/usr/bin/osascript', '-e']
    assert 'quoted form of (item 1 of argv)' in args[2]
    assert 'quoted form of (item 2 of argv)' in args[2]
    assert args[3:] == [str(helper), str(data)]


def test_mac_setup_and_panel_actions_report_missing_resources_without_launch(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop.sys, 'platform', 'darwin')
    monkeypatch.setattr(desktop, '_resource_path', Mock(return_value=tmp_path / 'missing'))
    launch = Mock()
    monkeypatch.setattr(desktop.subprocess, 'Popen', launch)
    with pytest.raises(ValueError, match='helper is missing'):
        desktop._setup_premiere_mac(tmp_path)
    with pytest.raises(ValueError, match='package is missing'):
        desktop._install_premiere_panel()
    launch.assert_not_called()


@pytest.mark.parametrize('platform', ['win32', 'darwin'])
def test_frozen_premiere_install_opens_bundled_ccx(monkeypatch, tmp_path, platform):
    package = tmp_path / 'plugins' / 'VideoAnalyzer-Premiere-0.1.2.ccx'
    package.parent.mkdir()
    package.write_bytes(b'test package')
    monkeypatch.setattr(desktop.sys, 'platform', platform)
    monkeypatch.setattr(desktop.sys, 'frozen', True, raising=False)
    resource = Mock(return_value=package)
    monkeypatch.setattr(desktop, '_resource_path', resource)
    startfile = Mock()
    monkeypatch.setattr(desktop.os, 'startfile', startfile, raising=False)
    launch = Mock()
    monkeypatch.setattr(desktop.subprocess, 'Popen', launch)
    desktop._install_premiere_panel()
    resource.assert_called_once_with('plugins/VideoAnalyzer-Premiere-0.1.2.ccx')
    if platform == 'win32':
        startfile.assert_called_once_with(str(package))
        launch.assert_not_called()
    else:
        launch.assert_called_once_with(['/usr/bin/open', str(package)])
        startfile.assert_not_called()


def _resolve_install_resources(tmp_path, suffix):
    plugin = tmp_path / "App's $(touch ignored); & public plugin"
    plugin.mkdir()
    (plugin / 'manifest.xml').write_text('<test-fixture />', encoding='utf-8')
    helper = tmp_path / ("Install's $(touch ignored); & Resolve" + suffix)
    helper.write_text('# test placeholder', encoding='utf-8')
    return plugin, helper


def test_windows_resolve_launcher_passes_literal_argv_and_hides_its_process(monkeypatch, tmp_path):
    plugin, helper = _resolve_install_resources(tmp_path, '.ps1')
    monkeypatch.setattr(desktop.sys, 'platform', 'win32')
    monkeypatch.setattr(desktop.sys, 'frozen', False, raising=False)
    resource = Mock(side_effect=lambda relative: helper if relative.endswith('.ps1') else plugin)
    monkeypatch.setattr(desktop, '_resource_path', resource)
    monkeypatch.setattr(desktop.subprocess, 'CREATE_NO_WINDOW', 0x08000000, raising=False)
    launch = Mock()
    monkeypatch.setattr(desktop.subprocess, 'Popen', launch)
    desktop._install_resolve_panel()
    assert [call.args[0] for call in resource.call_args_list] == [
        'integrations/resolve', 'packaging/resolve/install-resolve.ps1',
    ]
    launch.assert_called_once_with([
        'powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(helper.resolve()),
        '-PluginPath', str(plugin.resolve()),
    ], creationflags=0x08000000)
    assert 'shell' not in launch.call_args.kwargs


def test_mac_resolve_launcher_quotes_arguments_without_interpolating_paths(monkeypatch, tmp_path):
    plugin, helper = _resolve_install_resources(tmp_path, '.command')
    monkeypatch.setattr(desktop.sys, 'platform', 'darwin')
    monkeypatch.setattr(desktop.sys, 'frozen', False, raising=False)
    resource = Mock(side_effect=lambda relative: helper if relative.endswith('.command') else plugin)
    monkeypatch.setattr(desktop, '_resource_path', resource)
    launch = Mock()
    monkeypatch.setattr(desktop.subprocess, 'Popen', launch)
    desktop._install_resolve_panel()
    assert [call.args[0] for call in resource.call_args_list] == [
        'integrations/resolve', 'packaging/resolve/install-resolve-macos.command',
    ]
    argv = launch.call_args.args[0]
    assert argv[:2] == ['/usr/bin/osascript', '-e']
    assert argv[3:] == [str(helper.resolve()), str(plugin.resolve())]
    assert 'quoted form of (item 1 of argv)' in argv[2]
    assert 'quoted form of (item 2 of argv)' in argv[2]
    assert str(helper) not in argv[2] and str(plugin) not in argv[2]
    assert 'touch ignored' not in argv[2]
    assert launch.call_args.kwargs == {}


@pytest.mark.parametrize('platform, suffix', [('win32', '.ps1'), ('darwin', '.command')])
def test_frozen_resolve_launcher_uses_bundle_resources_and_resolves_crosslinks(monkeypatch, tmp_path, platform, suffix):
    plugin, helper = _resolve_install_resources(tmp_path, suffix)
    monkeypatch.setattr(desktop.sys, 'platform', platform)
    monkeypatch.setattr(desktop.sys, 'frozen', True, raising=False)
    # Model PyInstaller Resources/Frameworks cross-links without creating links
    # or running any platform command. The installer receives their resolved paths.
    linked_plugin = SimpleNamespace(resolve=Mock(return_value=plugin))
    linked_helper = SimpleNamespace(resolve=Mock(return_value=helper))
    resource = Mock(side_effect=lambda relative: linked_helper if relative.endswith(suffix) else linked_plugin)
    monkeypatch.setattr(desktop, '_resource_path', resource)
    monkeypatch.setattr(desktop.subprocess, 'CREATE_NO_WINDOW', 0x08000000, raising=False)
    launch = Mock()
    monkeypatch.setattr(desktop.subprocess, 'Popen', launch)
    desktop._install_resolve_panel()
    assert [call.args[0] for call in resource.call_args_list] == [
        'resolve-panel', 'desktop-docs/install-resolve' + ('-macos.command' if platform == 'darwin' else '.ps1'),
    ]
    linked_plugin.resolve.assert_called_once_with()
    linked_helper.resolve.assert_called_once_with()
    argv = launch.call_args.args[0]
    assert str(helper) in argv and str(plugin) in argv


@pytest.mark.parametrize('platform, suffix', [('win32', '.ps1'), ('darwin', '.command')])
@pytest.mark.parametrize('missing', ['helper', 'manifest'])
def test_resolve_launcher_missing_resources_fails_before_spawning(monkeypatch, tmp_path, platform, suffix, missing):
    plugin, helper = _resolve_install_resources(tmp_path, suffix)
    if missing == 'helper':
        helper.unlink()
    else:
        (plugin / 'manifest.xml').unlink()
    monkeypatch.setattr(desktop.sys, 'platform', platform)
    monkeypatch.setattr(desktop.sys, 'frozen', False, raising=False)
    monkeypatch.setattr(desktop, '_resource_path', Mock(side_effect=lambda relative: helper if relative.endswith(suffix) else plugin))
    launch = Mock()
    monkeypatch.setattr(desktop.subprocess, 'Popen', launch)
    with pytest.raises(ValueError, match='Resolve integration resources are missing'):
        desktop._install_resolve_panel()
    launch.assert_not_called()


def test_resolve_launcher_unsupported_platform_fails_without_spawning(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop.sys, 'platform', 'linux')
    monkeypatch.setattr(desktop.sys, 'frozen', False, raising=False)
    monkeypatch.setattr(desktop, '_resource_path', Mock(return_value=tmp_path / 'unused'))
    launch = Mock()
    monkeypatch.setattr(desktop.subprocess, 'Popen', launch)
    with pytest.raises(ValueError, match='supports Windows and Apple Silicon Mac'):
        desktop._install_resolve_panel()
    launch.assert_not_called()
