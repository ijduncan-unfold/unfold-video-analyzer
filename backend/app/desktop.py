"""Desktop companion: run the local engine, open its UI, and own its lifetime.

Keep app imports inside main(): desktop storage and env-file isolation must be
configured before Settings is initialized. No developer database or key is used.
"""
from __future__ import annotations

import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser


def default_data_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "VideoAnalyzer"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "VideoAnalyzer"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "video-analyzer"


def configure_environment(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["VIDEO_ANALYZER_DESKTOP"] = "1"
    os.environ["DATABASE_PATH"] = str(data_dir / "library.sqlite3")
    os.environ["UPLOAD_DIR"] = str(data_dir / "media")
    os.environ["EDITOR_TOKEN_PATH"] = str(data_dir / "premiere-token.txt")
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


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=1) as response:
        return json.load(response)


def _open_ui(base_url: str) -> None:
    # The fragment stays in the local browser and is removed by the frontend
    # after bootstrapping settings access; it is never sent in server URLs.
    from app.services.editor_bridge import get_pairing_token
    webbrowser.open(base_url + "/#desktop-token=" + get_pairing_token())


def _open_data_folder(data_dir: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(data_dir))
    elif sys.platform == "darwin":
        subprocess.Popen(["/usr/bin/open", str(data_dir)])
    else:
        subprocess.Popen(["xdg-open", str(data_dir)])


def _tray_image():
    from PIL import Image, ImageDraw
    image = Image.new("RGBA", (64, 64), (15, 23, 42, 255))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((8, 13, 56, 51), radius=6, outline=(96, 165, 250), width=4)
    draw.polygon([(26, 22), (26, 43), (44, 32)], fill=(147, 197, 253))
    return image


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Video Analyzer desktop companion")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--pair-premiere", action="store_true", help="Open your local Premiere pairing token")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-tray", action="store_true", help="Run until interrupted, without a tray icon")
    parser.add_argument("--smoke-test", action="store_true", help="Start, check health and frontend, then stop")
    args = parser.parse_args(argv)
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
        return 0

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # Never attach to or terminate another server just because it uses 8000.
        if sys.platform == "win32":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", args.port))
        listener.listen(128)
    except OSError:
        listener.close()
        message = (f"Port {args.port} is already in use. If Video Analyzer is already running, "
                   "open it from the system tray. Otherwise close the development server or "
                   "other app using this port, then open Video Analyzer again.")
        if args.smoke_test:
            log.error(message)
            return 1
        show_message(message, error=True)
        return 1

    import uvicorn
    from app.main import app
    # Force generation once so the standalone pairing shortcut and panel share
    # the same token. Its contents are never included in logs.
    from app.services.editor_bridge import get_pairing_token
    get_pairing_token()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port,
                                         log_config=None, access_log=False, timeout_graceful_shutdown=15))
    failures: list[BaseException] = []

    def serve():
        try:
            server.run(sockets=[listener])
        except BaseException as exc:
            failures.append(exc)
            log.exception("Local engine stopped unexpectedly")

    worker = threading.Thread(target=serve, name="video-analyzer-engine", daemon=True)
    worker.start()
    base_url = f"http://127.0.0.1:{args.port}"
    try:
        deadline = time.monotonic() + 30
        while not server.started and not failures and worker.is_alive() and time.monotonic() < deadline:
            time.sleep(0.1)
        if not server.started or failures:
            raise RuntimeError("The local engine could not start. See desktop.log in your Video Analyzer data folder.")
        if args.smoke_test:
            health = _get_json(base_url + "/api/health")
            capabilities = _get_json(base_url + "/api/capabilities")
            with urllib.request.urlopen(base_url + "/", timeout=5) as response:
                html = response.read()
                if b'<div id="root">' not in html:
                    raise RuntimeError("Bundled frontend was not served")
            for asset_path in re.findall(rb'(?:src|href)="(/assets/[^\"]+)"', html):
                with urllib.request.urlopen(base_url + asset_path.decode("utf-8"), timeout=5) as response:
                    if not response.read() or response.headers.get_content_type() == "text/html":
                        raise RuntimeError("A bundled frontend asset is missing")
            if health.get("status") != "ok":
                raise RuntimeError("Health check failed")
            report = {"status": "ok", "frontend": "ok", "local_media": capabilities.get("local_media"),
                      "google_configured": capabilities.get("google_configured")}
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
                    log.exception("Unable to open Premiere pairing file")
                    show_message("Could not open the pairing file. See desktop.log for details.", error=True)

            icon = pystray.Icon("video-analyzer", _tray_image(), "Video Analyzer", menu=pystray.Menu(
                pystray.MenuItem("Open Video Analyzer", lambda icon, item: _open_ui(base_url), default=True),
                pystray.MenuItem("Pair with Premiere", pair),
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
        handler.close()
    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
    except Exception:
        logging.getLogger("desktop").exception("Desktop launcher failed")
        if "--smoke-test" not in sys.argv:
            show_message("Video Analyzer could not start. See desktop.log in your Video Analyzer data folder.", error=True)
        exit_code = 1
    raise SystemExit(exit_code)
