"""Desktop companion: run the local engine, open its UI, and own its lifetime.

Keep app imports inside main(): desktop storage and env-file isolation must be
configured before Settings is initialized. No developer database or key is used.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser
from app.desktop_tls import TLSSetupRequired, configured_tls, smoke_tls


def default_data_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "VideoAnalyzer"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "VideoAnalyzer"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "video-analyzer"


def configure_environment(data_dir: Path) -> None:
    data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.environ["VIDEO_ANALYZER_DESKTOP"] = "1"
    os.environ["DATABASE_PATH"] = str(data_dir / "library.sqlite3")
    os.environ["UPLOAD_DIR"] = str(data_dir / "media")
    os.environ["EDITOR_TOKEN_PATH"] = str(data_dir / "premiere-token.txt")
    if data_dir.resolve() != default_data_dir().expanduser().resolve():
        # A test/custom library never replaces the normal companion's connection.
        os.environ['EDITOR_DISCOVERY_PATH'] = str(data_dir / 'editor-service.json')
    if getattr(sys, "frozen", False):
        os.environ["VIDEO_ANALYZER_FRONTEND_DIR"] = str(Path(sys._MEIPASS) / "frontend" / "dist")
    # A frozen app has no useful terminal PATH on macOS; Windows WinGet can also
    # install FFmpeg before a new desktop session receives the PATH update.
    media_dirs = []
    if sys.platform == "win32":
        packages = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
        if packages.is_dir():
            for package in packages.glob("Gyan.FFmpeg_*"):
                media_dirs.extend(package.glob("ffmpeg*/bin"))
    elif sys.platform == "darwin":
        media_dirs = [Path("/opt/homebrew/bin"), Path("/usr/local/bin")]
    os.environ["PATH"] = os.pathsep.join([str(p) for p in media_dirs] + [os.environ.get("PATH", "")])


def show_message(message: str, *, error: bool = False) -> None:
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "Video Analyzer", 0x10 if error else 0x40)
    elif sys.platform == "darwin":
        script = 'on run argv\ndisplay dialog (item 1 of argv) with title "Video Analyzer" buttons {"OK"} default button "OK"\nend run'
        subprocess.run(["/usr/bin/osascript", "-e", script, message], check=False)
    elif sys.stderr:
        print(message, file=sys.stderr)


def show_pairing_token() -> None:
    # This is deliberately a user-requested display, never a log or HTTP response.
    from app.config import settings
    from app.services.editor_bridge import get_pairing_token
    get_pairing_token()
    token_path = Path(settings.editor_token_path)
    if sys.platform == "win32":
        subprocess.Popen(["notepad.exe", str(token_path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["/usr/bin/open", "-a", "TextEdit", str(token_path)])
    else:
        webbrowser.open(token_path.as_uri())


def _get_json(url: str, context: ssl.SSLContext | None = None, headers=None) -> dict:
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=5, context=context) as response:
        return json.load(response)


def _open_ui(base_url: str) -> None:
    # The fragment stays in the desktop window and is removed by the frontend
    # after bootstrapping settings access; it is never sent in server URLs.
    from app.services.editor_bridge import get_pairing_token
    from app.desktop_window import launch_window
    launch_window(base_url, get_pairing_token())


def _open_data_folder(data_dir: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(data_dir))
    elif sys.platform == "darwin":
        subprocess.Popen(["/usr/bin/open", str(data_dir)])
    else:
        subprocess.Popen(["xdg-open", str(data_dir)])


def _resource_path(relative: str) -> Path:
    root = Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[2]
    return root / relative


def _setup_premiere_mac(data_dir: Path) -> None:
    if sys.platform != 'darwin':
        raise ValueError('This setup action is available on macOS.')
    helper = _resource_path('desktop-docs/setup-premiere-macos.command') if getattr(sys, 'frozen', False) else _resource_path('packaging/macos/setup-premiere.command')
    if not helper.is_file():
        raise ValueError('The Mac setup helper is missing. Run scripts/setup-desktop-macos.command from a checkout.')
    # The helper needs a visible Terminal for Homebrew and macOS trust prompts.
    script = '''on run argv
tell application "Terminal"
activate
do script "/bin/bash " & quoted form of (item 1 of argv) & " " & quoted form of (item 2 of argv)
end tell
end run'''
    subprocess.Popen(['/usr/bin/osascript', '-e', script, str(helper), str(data_dir)])


def _offer_mac_setup(message: str, data_dir: Path) -> None:
    script = '''on run argv
display dialog (item 1 of argv) with title "Video Analyzer" buttons {"Quit", "Set up Premiere"} default button "Set up Premiere"
return button returned of result
end run'''
    reply = subprocess.run(['/usr/bin/osascript', '-e', script, message], capture_output=True, text=True, check=False)
    if reply.returncode == 0 and reply.stdout.strip() == 'Set up Premiere':
        _setup_premiere_mac(data_dir)


def _install_premiere_panel() -> None:
    package = _resource_path('plugins/VideoAnalyzer-Premiere-0.1.0.ccx') if getattr(sys, 'frozen', False) else _resource_path('dist/installers/VideoAnalyzer-Premiere-0.1.0.ccx')
    if not package.is_file() and getattr(sys, 'frozen', False) and sys.platform == 'win32':
        package = Path(sys.executable).parent / 'plugins' / 'VideoAnalyzer-Premiere-0.1.0.ccx'
    if not package.is_file():
        raise ValueError('The Premiere package is missing. Build the plugin package first.')
    if sys.platform == 'darwin':
        subprocess.Popen(['/usr/bin/open', str(package)])
    elif sys.platform == 'win32':
        os.startfile(str(package))


def _install_resolve_panel() -> None:
    frozen = getattr(sys, 'frozen', False)
    # PyInstaller cross-links Mac Resources/Frameworks. Pass the actual data
    # directory so the installer can still refuse arbitrary filesystem links.
    plugin = _resource_path('resolve-panel' if frozen else 'integrations/resolve').resolve()
    if sys.platform == 'win32':
        helper = _resource_path('desktop-docs/install-resolve.ps1' if frozen else 'packaging/resolve/install-resolve.ps1').resolve()
        if not helper.is_file() or not (plugin / 'manifest.xml').is_file():
            raise ValueError('The Resolve integration resources are missing. Build the current companion app.')
        subprocess.Popen(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(helper),
                          '-PluginPath', str(plugin)], creationflags=subprocess.CREATE_NO_WINDOW)
    elif sys.platform == 'darwin':
        helper = _resource_path('desktop-docs/install-resolve-macos.command' if frozen else 'packaging/resolve/install-resolve-macos.command').resolve()
        if not helper.is_file() or not (plugin / 'manifest.xml').is_file():
            raise ValueError('The Resolve integration resources are missing. Build the current companion app.')
        script = '''on run argv
tell application "Terminal"
activate
do script "/bin/bash " & quoted form of (item 1 of argv) & " " & quoted form of (item 2 of argv)
end tell
end run'''
        subprocess.Popen(['/usr/bin/osascript', '-e', script, str(helper), str(plugin)])
    else:
        raise ValueError('This Resolve integration supports Windows and Apple Silicon Mac.')


def _tray_image():
    from PIL import Image, ImageDraw
    image = Image.new("RGBA", (64, 64), (15, 23, 42, 255))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((8, 13, 56, 51), radius=6, outline=(96, 165, 250), width=4)
    draw.polygon([(26, 22), (26, 43), (44, 32)], fill=(147, 197, 253))
    return image


def main(argv: list[str] | None = None) -> int:
    worker_args = list(sys.argv[1:] if argv is None else argv)
    if worker_args[:1] == ['--sony-raw-worker']:
        from app.services.sony_raw_worker import main as run_sony_worker
        return run_sony_worker(worker_args[1:])
    parser = argparse.ArgumentParser(description="Video Analyzer desktop companion")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--https", action="store_true", help="Use verified local HTTPS")
    parser.add_argument("--http", action="store_true", help="Use HTTP for standalone/development (Mac Premiere requires HTTPS)")
    parser.add_argument("--tls-cert", type=Path)
    parser.add_argument("--tls-key", type=Path)
    parser.add_argument("--tls-ca", type=Path)
    parser.add_argument("--pair-premiere", "--pair-editor", "--pair-resolve", action="store_true", help="Open your local editor pairing token")
    parser.add_argument("--setup-premiere", action="store_true", help="Open explicit Mac HTTPS setup in Terminal")
    parser.add_argument("--install-premiere-panel", action="store_true", help="Open the bundled panel in Creative Cloud")
    parser.add_argument("--install-resolve-panel", action="store_true", help="Install the Resolve Studio workflow integration")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-tray", action="store_true", help="Run until interrupted, without a tray icon")
    parser.add_argument("--smoke-test", action="store_true", help="Start, check health and frontend, then stop")
    parser.add_argument("--desktop-window", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.desktop_window:
        from app.desktop_window import run_window
        run_window(os.environ['VIDEO_ANALYZER_WINDOW_URL'], os.environ.get('VIDEO_ANALYZER_WINDOW_PICK') == '1')
        return 0
    if args.http and (args.https or args.tls_cert or args.tls_key or args.tls_ca):
        parser.error('--http cannot be combined with HTTPS certificate options')
    use_https = not args.http and (sys.platform == 'darwin' or args.https or any((args.tls_cert, args.tls_key, args.tls_ca)))
    args.port = args.port if args.port is not None else (8443 if use_https else 8000)
    if not 1024 <= args.port <= 65535:
        parser.error("Port must be between 1024 and 65535")
    data_dir = args.data_dir.expanduser().resolve()
    configure_environment(data_dir)
    handler = RotatingFileHandler(data_dir / "desktop.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    # The API's lifespan creates a StreamHandler when it has no handler. A
    # windowed executable has no stderr; keep application diagnostics in the log.
    logging.getLogger("app").addHandler(handler)
    log = logging.getLogger("desktop")
    if args.pair_premiere:
        show_pairing_token()
        handler.close()
        return 0
    if args.setup_premiere:
        _setup_premiere_mac(data_dir)
        handler.close()
        return 0
    if args.install_premiere_panel:
        _install_premiere_panel()
        handler.close()
        return 0
    if args.install_resolve_panel:
        try:
            _install_resolve_panel()
        finally:
            handler.close()
        return 0

    resources = ExitStack()
    if args.smoke_test:
        discovery_dir = Path(resources.enter_context(tempfile.TemporaryDirectory(prefix='video-analyzer-discovery-smoke-')))
        os.environ['EDITOR_DISCOVERY_PATH'] = str(discovery_dir / 'editor-service.json')
    tls = None
    try:
        if use_https:
            tls = smoke_tls(resources) if args.smoke_test and not any((args.tls_cert, args.tls_key, args.tls_ca)) else configured_tls(data_dir, args.tls_cert, args.tls_key, args.tls_ca)
        tls_options = tls.server_options() if tls else {}
        client_context = tls.client_context() if tls else None
    except TLSSetupRequired as exc:
        resources.close()
        log.error('Local HTTPS setup is required')
        if sys.platform == 'darwin' and not args.smoke_test:
            _offer_mac_setup(str(exc), data_dir)
        elif not args.smoke_test:
            show_message(str(exc), error=True)
        handler.close()
        return 1
    except Exception:
        resources.close()
        raise

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # Never attach to or terminate another server just because it uses 8000.
        if sys.platform == "win32":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", args.port))
        listener.listen(128)
    except OSError:
        listener.close()
        resources.close()
        message = (f"Port {args.port} is already in use. If Video Analyzer is already running, "
                   "open it from the system tray. Otherwise close the development server or "
                   "other app using this port, then open Video Analyzer again.")
        if args.smoke_test:
            log.error(message)
            handler.close()
            return 1
        show_message(message, error=True)
        handler.close()
        return 1

    import uvicorn
    from app.main import app
    base_url = f"{'https://localhost' if tls else 'http://127.0.0.1'}:{args.port}"
    app.state.editor_connection_server = base_url
    app.state.editor_connection_ssl_context = client_context
    # Force generation once so the standalone pairing shortcut and panel share
    # the same token. Its contents are never included in logs.
    from app.services.editor_bridge import get_pairing_token
    get_pairing_token()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port,
                                         log_config=None, access_log=False, timeout_graceful_shutdown=15, **tls_options))
    failures: list[BaseException] = []

    def serve():
        try:
            server.run(sockets=[listener])
        except BaseException as exc:
            failures.append(exc)
            log.exception("Local engine stopped unexpectedly")

    worker = threading.Thread(target=serve, name="video-analyzer-engine", daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 30
        while not server.started and not failures and worker.is_alive() and time.monotonic() < deadline:
            time.sleep(0.1)
        if not server.started or failures:
            raise RuntimeError("The local engine could not start. See desktop.log in your Video Analyzer data folder.")
        if tls:
            _get_json(base_url + '/api/health', client_context)
        if args.smoke_test:
            health = _get_json(base_url + "/api/health", client_context)
            capabilities = _get_json(base_url + "/api/capabilities", client_context)
            with urllib.request.urlopen(base_url + "/", timeout=5, context=client_context) as response:
                html = response.read()
                if b'<div id="root">' not in html:
                    raise RuntimeError("Bundled frontend was not served")
            for asset_path in re.findall(rb'(?:src|href)="(/assets/[^\"]+)"', html):
                with urllib.request.urlopen(base_url + asset_path.decode("utf-8"), timeout=5, context=client_context) as response:
                    if not response.read() or response.headers.get_content_type() == "text/html":
                        raise RuntimeError("A bundled frontend asset is missing")
            if health.get("status") != "ok":
                raise RuntimeError("Health check failed")
            for editor_host in ('premiere', 'resolve'):
                editor_health = _get_json(base_url + f'/api/editor/{editor_host}/health', client_context,
                                          {'Authorization': 'Bearer ' + get_pairing_token()})
                if editor_health.get('status') != 'ok' or editor_health.get('host') != editor_host:
                    raise RuntimeError('A bundled editor API is missing')
            report = {"status": "ok", "frontend": "ok", "local_media": capabilities.get("local_media"),
                      "google_configured": capabilities.get("google_configured"),
                      "transport": 'https' if tls else 'http', 'certificate_verified': bool(tls),
                      'editor_hosts': ['premiere', 'resolve']}
            (data_dir / "smoke-test.json").write_text(json.dumps(report), encoding="utf-8")
            if sys.stdout:
                print(json.dumps(report))
            return 0
        if not args.no_browser:
            _open_ui(base_url)
        if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
            show_message("Video Analyzer is running, but FFmpeg is required to import and inspect media. "
                         "Install FFmpeg using the desktop setup guide, then quit and reopen Video Analyzer.")
        if args.no_tray:
            while worker.is_alive():
                time.sleep(0.5)
        else:
            import pystray

            def quit_app(icon, item):
                server.should_exit = True
                icon.stop()

            def pair(icon, item):
                try:
                    show_pairing_token()
                except Exception:
                    log.exception("Unable to open editor pairing file")
                    show_message("Could not open the pairing file. See desktop.log for details.", error=True)

            def install_resolve(icon, item):
                try:
                    _install_resolve_panel()
                except Exception:
                    log.exception('Unable to start Resolve installation')
                    show_message('Could not start Resolve installation. See desktop.log for details.', error=True)

            icon = pystray.Icon("video-analyzer", _tray_image(), "Video Analyzer", menu=pystray.Menu(
                pystray.MenuItem("Open Video Analyzer", lambda icon, item: _open_ui(base_url), default=True),
                pystray.MenuItem("Pair with editor", pair),
                pystray.MenuItem("Install Premiere panel", lambda icon, item: _install_premiere_panel(), visible=sys.platform == 'darwin'),
                pystray.MenuItem("Install Resolve integration", install_resolve),
                pystray.MenuItem("Set up Premiere HTTPS", lambda icon, item: _setup_premiere_mac(data_dir), visible=sys.platform == 'darwin'),
                pystray.MenuItem("Open data folder", lambda icon, item: _open_data_folder(data_dir)),
                pystray.MenuItem("Quit", quit_app),
            ))
            icon.run()
    except KeyboardInterrupt:
        pass
    except Exception:
        log.exception("Desktop startup failed")
        if args.smoke_test:
            return 1
        show_message("Video Analyzer could not start. See desktop.log in your Video Analyzer data folder.", error=True)
        return 1
    finally:
        server.should_exit = True
        worker.join(timeout=20)
        listener.close()
        resources.close()
        handler.close()
    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
    except Exception:
        logging.getLogger("desktop").exception("Desktop launcher failed")
        if "--smoke-test" not in sys.argv and "--sony-raw-worker" not in sys.argv:
            show_message("Video Analyzer could not start. See desktop.log in your Video Analyzer data folder.", error=True)
        exit_code = 1
    raise SystemExit(exit_code)
