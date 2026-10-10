"""Private same-user discovery of the running local editor service."""
import asyncio
from contextlib import contextmanager
import csv
from functools import lru_cache
import json
import logging
import os
from pathlib import Path
import re
import ssl
import stat
import subprocess
import sys
import threading
import time
import urllib.request
import uuid

from app.config import settings
from app.services.editor_bridge import get_pairing_token

_log = logging.getLogger('app')
_file_lock = threading.RLock()
READY_TIMEOUT = 30
READY_INTERVAL = 0.5
ROTATION_INTERVAL = 1


def default_discovery_path():
    if sys.platform == 'win32':
        directory = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'VideoAnalyzer'
    elif sys.platform == 'darwin':
        directory = Path.home() / 'Library' / 'Application Support' / 'VideoAnalyzer'
    else:
        directory = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share')) / 'video-analyzer'
    return directory / 'editor-service.json'


def discovery_path():
    path = Path(settings.editor_discovery_path).expanduser() if settings.editor_discovery_path else default_discovery_path()
    if not path.is_absolute():
        raise ValueError('Editor discovery requires an absolute local path')
    return Path(os.path.abspath(path))


def normalize_server(value):
    match = re.fullmatch(r'(http|https)://(127\.0\.0\.1|localhost):(8000|8443)/?', str(value).strip(), re.I)
    if not match or (match[1].lower(), match[3]) not in (('http', '8000'), ('https', '8443')):
        raise ValueError('Editor discovery requires the supported local service address')
    return f'{match[1].lower()}://{match[2].lower()}:{match[3]}'


def _check_path(path):
    """Inspect lexical ancestors: resolving a link would hide its unsafe origin."""
    for item in reversed((path, *path.parents)):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Editor discovery refuses linked or reparse paths')
        if item != path and not stat.S_ISDIR(info.st_mode):
            raise ValueError('Editor discovery parent is not a directory')
        if item == path and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
            raise ValueError('Editor discovery requires an unlinked regular file')
    if os.name != 'nt' and path.parent.exists():
        info = path.parent.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ValueError('Editor discovery directory is not private to this user')


@lru_cache(maxsize=1)
def _windows_user_sid():
    result = subprocess.run(['whoami.exe', '/user', '/fo', 'csv', '/nh'], capture_output=True,
                            text=True, check=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
    rows = list(csv.reader(result.stdout.splitlines()))
    if len(rows) != 1 or len(rows[0]) != 2 or not re.fullmatch(r'S-1-[0-9-]+', rows[0][1]):
        raise OSError('Unable to establish private editor discovery permissions')
    return rows[0][1]


def _make_private(path, directory=False):
    if os.name == 'nt':
        grant = f'*{_windows_user_sid()}:' + ('(OI)(CI)F' if directory else 'F')
        subprocess.run(['icacls.exe', str(path), '/inheritance:r', '/grant:r', grant],
                       capture_output=True, check=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        os.chmod(path, 0o700 if directory else 0o600)


def _prepare_directory(path):
    _check_path(path)
    for directory in reversed(path.parents):
        if not directory.exists():
            try:
                directory.mkdir(mode=0o700)
                _make_private(directory, directory=True)
            except FileExistsError:
                pass
    _check_path(path)


@contextmanager
def _locked(path):
    """Cooperating instances cannot remove or refresh a successor's descriptor."""
    with _file_lock:
        _prepare_directory(path)
        lock_path = path.with_name(f'.{path.name}.lock')
        _check_path(lock_path)
        flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
        try:
            fd = os.open(lock_path, flags, 0o600)
            try:
                _make_private(lock_path)
                os.write(fd, b'0')
            except BaseException:
                os.close(fd)
                raise
        except FileExistsError:
            fd = os.open(lock_path, os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0))
        try:
            if os.name == 'nt':
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                _check_path(path)
                yield
            finally:
                if os.name == 'nt':
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def probe_instance(server, token, instance_id, ssl_context=None):
    """Never send the bearer token outside the fixed local origin, even on redirect."""
    try:
        server = normalize_server(server)
        handlers = [urllib.request.ProxyHandler({}), _NoRedirect()]
        if server.startswith('https:'):
            context = ssl_context or ssl.create_default_context()
            if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
                return False
            handlers.append(urllib.request.HTTPSHandler(context=context))
        opener = urllib.request.build_opener(*handlers)
        request = urllib.request.Request(server + '/api/editor/resolve/health',
                                         headers={'Authorization': 'Bearer ' + token})
        with opener.open(request, timeout=1) as response:
            data = json.loads(response.read(4097))
        return (isinstance(data, dict) and data.get('status') == 'ok' and data.get('host') == 'resolve' and
                data.get('protocol_version') == 1 and data.get('instance_id') == instance_id)
    except (OSError, ValueError, TypeError):
        return False


class EditorDiscovery:
    def __init__(self, path, server, instance_id, ssl_context=None):
        self.path = Path(path)
        self.server = normalize_server(server)
        self.instance_id = str(uuid.UUID(instance_id))
        self.ssl_context = ssl_context
        self._stopped = threading.Event()
        self._published_token = None

    def _owned(self):
        _check_path(self.path)
        try:
            with self.path.open('r', encoding='utf-8') as source:
                data = json.loads(source.read(4097))
            return isinstance(data, dict) and data.get('instance_id') == self.instance_id
        except (FileNotFoundError, ValueError, TypeError):
            return False

    def _publish(self, token, require_owned=False):
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
            raise ValueError('Invalid local editor credential')
        with _locked(self.path):
            if self._stopped.is_set() or require_owned and not self._owned():
                return False
            temporary = self.path.with_name(f'.{self.path.name}.{uuid.uuid4().hex}.tmp')
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            try:
                _make_private(temporary)  # Restrict the empty file before any token bytes are written.
                with os.fdopen(fd, 'w', encoding='utf-8') as output:
                    fd = None
                    json.dump({'version': 1, 'server': self.server, 'token': token,
                               'instance_id': self.instance_id}, output)
                    output.flush()
                    os.fsync(output.fileno())
                _check_path(self.path)
                os.replace(temporary, self.path)
                self._published_token = token
                return True
            finally:
                if fd is not None:
                    os.close(fd)
                temporary.unlink(missing_ok=True)

    async def run(self):
        try:
            deadline = time.monotonic() + READY_TIMEOUT
            while not self._stopped.is_set() and time.monotonic() < deadline:
                token = get_pairing_token()
                if await asyncio.to_thread(probe_instance, self.server, token, self.instance_id, self.ssl_context):
                    if await asyncio.to_thread(self._publish, token):
                        break
                    return
                await asyncio.sleep(READY_INTERVAL)
            else:
                _log.warning('Automatic editor connection unavailable; local service readiness was not verified')
                return
            while not self._stopped.is_set():
                await asyncio.sleep(ROTATION_INTERVAL)
                if not await asyncio.to_thread(self._is_owned):
                    return
                token = get_pairing_token()
                if token != self._published_token:
                    if not await asyncio.to_thread(probe_instance, self.server, token, self.instance_id, self.ssl_context):
                        continue
                    if not await asyncio.to_thread(self._publish, token, True):
                        return
        except (OSError, ValueError, subprocess.SubprocessError):
            _log.warning('Automatic editor connection unavailable; private local discovery could not be prepared')

    def _is_owned(self):
        with _locked(self.path):
            return self._owned()

    def stop(self):
        self._stopped.set()

    def remove_owned(self):
        try:
            with _locked(self.path):
                if self._owned():
                    self.path.unlink()
        except (OSError, ValueError, subprocess.SubprocessError):
            _log.warning('Automatic editor connection cleanup could not be completed')
