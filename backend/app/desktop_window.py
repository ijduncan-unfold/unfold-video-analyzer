"""Native file drops supply original paths; the web UI never uploads their bytes."""
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit

from app.utils.media_process import hidden_process_kwargs

_process = None


def local_origin(url):
    parsed = urlsplit(url)
    if (parsed.scheme not in {'http', 'https'} or parsed.hostname not in {'localhost', '127.0.0.1', '::1'}
            or parsed.username or parsed.password or parsed.path not in {'', '/'} or parsed.query):
        raise ValueError('The desktop window requires the local Video Analyzer origin.')
    return parsed.scheme, parsed.netloc


def launch_window(base_url, token='', pick=False):
    global _process
    local_origin(base_url)
    if _process is not None and _process.poll() is None:
        return
    # Verify the optional desktop runtime before reporting a successful launch.
    import importlib.util
    if importlib.util.find_spec('webview') is None:
        raise ValueError('Install the desktop dependencies to enable native file drops.')
    env = os.environ.copy()
    env['VIDEO_ANALYZER_WINDOW_URL'] = base_url.rstrip('/') + '/' + ('#desktop-token=' + token if token else '')
    env['VIDEO_ANALYZER_WINDOW_PICK'] = '1' if pick else '0'
    if getattr(sys, 'frozen', False):
        command = [sys.executable, '--desktop-window']
    else:
        backend = str(Path(__file__).resolve().parents[1])
        env['PYTHONPATH'] = os.pathsep.join([backend, env.get('PYTHONPATH', '')])
        command = [sys.executable, '-m', 'app.desktop', '--desktop-window']
    _process = subprocess.Popen(command, env=env, **hidden_process_kwargs())


class NativeFiles:
    def __init__(self, origin):
        self._origin = local_origin(origin)
        self._window = None

    def _trusted(self):
        try:
            return local_origin(self._window.get_current_url()) == self._origin
        except (ValueError, TypeError, AttributeError):
            return False

    def choose_files(self):
        if not self._trusted():
            raise ValueError('Open the local Video Analyzer window to select media.')
        import webview
        paths = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=True,
            file_types=('Video media (*.mp4;*.mov;*.mxf;*.mkv;*.avi;*.webm;*.m4v;*.mpg;*.mpeg;*.mts;*.m2ts;*.braw;*.r3d;*.ari;*.arx;*.crm)', 'All files (*.*)'))
        return list(paths or [])

    def _drop(self, event):
        if not self._trusted():
            return
        # pywebview supplies these paths from the native OS drop, not guessed names.
        files = event.get('dataTransfer', {}).get('files', [])
        paths = [item.get('pywebviewFullPath') for item in files if isinstance(item, dict)]
        paths = list(dict.fromkeys(path for path in paths if isinstance(path, str) and Path(path).is_absolute()))
        self._dispatch(paths)

    def choose_folders(self):
        if not self._trusted():
            raise ValueError('Open the local Video Analyzer window to select folders.')
        from app.native_picker import choose_folders
        return choose_folders()

    def _dispatch(self, paths):
        if paths and self._trusted():
            payload = json.dumps(paths, ensure_ascii=True)
            self._window.evaluate_js("window.dispatchEvent(new CustomEvent('video-analyzer-files', {detail: " + payload + "}))")


def run_window(url, pick=False):
    local_origin(url)
    import webview
    from webview.dom import DOMEventHandler
    webview.settings['ALLOW_FILE_URLS'] = False
    webview.settings['ALLOW_DOWNLOADS'] = True
    webview.settings['IGNORE_SSL_ERRORS'] = False
    api = NativeFiles(url)
    window = webview.create_window('Video Analyzer', url, js_api=api, width=1440, height=920,
                                   min_size=(800, 600), background_color='#17191b')
    api._window = window
    first_load = True

    def bind():
        nonlocal first_load
        if not api._trusted():
            window.destroy()
            return
        window.dom.document.events.dragover += DOMEventHandler(lambda event: None, prevent_default=True)
        window.dom.document.events.drop += DOMEventHandler(api._drop, prevent_default=True)
        # Do not expose native APIs to external navigation. External links use the browser.
        window.evaluate_js("document.addEventListener('click', event => { const a = event.target.closest('a'); if (a && new URL(a.href, location.href).origin !== location.origin) a.target = '_blank'; });")
        if first_load and pick:
            api._dispatch(api.choose_files())
        first_load = False

    window.events.loaded += bind
    from app.config import settings
    storage = Path(settings.database_path).resolve().parent / 'desktop-webview'
    webview.start(gui='edgechromium' if sys.platform == 'win32' else 'cocoa' if sys.platform == 'darwin' else None,
                  private_mode=False, storage_path=str(storage))
