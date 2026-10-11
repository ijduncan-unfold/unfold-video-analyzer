"""Route verified camera formats to isolated decoders and app-owned viewing media."""
import asyncio
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
import uuid
import zipfile

from app.config import settings
from app.services.media_probe import probe_media
from app.services.sony_raw_worker import SONY_DLL_SHA256
from app.utils.media_process import hidden_process_kwargs


def supports_sony_f55(path, technical):
    return (Path(path).suffix.lower() == '.mxf' and
            str(technical.get('codec') or '').upper().startswith('F55RAW'))


def _sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def sony_runtime():
    """Prefer launcher resources; a source checkout unpacks only required DLLs.

    Never search arbitrary PATH directories or require a separate installed editor.
    The native ABI is pinned to the DLL verified by the F55 frame experiment.
    """
    if sys.platform != 'win32':
        raise ValueError('Sony F55 RAW decoding is available in the Windows x64 build')
    configured = os.environ.get('VIDEO_ANALYZER_DECODER_DIR')
    if configured:
        root = Path(configured).resolve()
        dll, shared = root / 'sony' / 'SonyRawDev.dll', root / 'shared'
        if dll.is_file() and shared.is_dir() and _sha(dll) == SONY_DLL_SHA256:
            return dll, shared
        raise ValueError('The configured Sony decoder bundle is missing or is an unverified version')
    if getattr(sys, 'frozen', False):
        root = Path(sys.executable).resolve().parent.parent / 'camera-decoders'
        dll, shared = root / 'sony' / 'SonyRawDev.dll', root / 'shared'
        if dll.is_file() and shared.is_dir() and _sha(dll) == SONY_DLL_SHA256:
            return dll, shared
    repo = Path(__file__).resolve().parents[3]
    archive = repo / 'portable' / 'VideoAnalyzer-windows-x64.zip'
    cache = Path(settings.database_path).resolve().parent / 'camera-decoders' / SONY_DLL_SHA256
    dll, shared = cache / 'sony' / 'SonyRawDev.dll', cache / 'shared'
    complete = cache / 'complete.json'
    if complete.is_file() and dll.is_file() and shared.is_dir() and _sha(dll) == SONY_DLL_SHA256:
        return dll, shared
    if not archive.is_file() or not zipfile.is_zipfile(archive):
        raise ValueError('Download the Windows portable bundle with git lfs pull to enable Sony F55 RAW')
    with zipfile.ZipFile(archive) as bundle:
        manifest = json.loads(bundle.read('manifest.json'))
        entries = {entry['path'].replace('\\', '/'): entry for entry in manifest['files']}
        wanted = [name for name in entries if name == 'camera-decoders/sony/SonyRawDev.dll' or
                  name.startswith('camera-decoders/shared/')]
        if entries.get('camera-decoders/sony/SonyRawDev.dll', {}).get('sha256') != SONY_DLL_SHA256:
            raise ValueError('The bundled Sony decoder version is unverified')
        for name in wanted:
            relative = Path(name).relative_to('camera-decoders')
            destination = (cache / relative).resolve()
            if not destination.is_relative_to(cache.resolve()) or entries[name]['bytes'] > 16 * 1024 * 1024:
                raise ValueError('Invalid bundled decoder resource')
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(destination.name + '.' + uuid.uuid4().hex + '.tmp')
            try:
                with bundle.open(name) as source, temporary.open('wb') as out:
                    shutil.copyfileobj(source, out)
                if _sha(temporary) != entries[name]['sha256']:
                    raise ValueError('Bundled decoder checksum mismatch')
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)
        complete.write_text(json.dumps({'sony_sha256': SONY_DLL_SHA256, 'resources': wanted}), encoding='utf-8')
    return dll, shared


async def stop_worker(proc):
    if proc.returncode is None:
        if os.name == 'nt':
            # This exact process is ours; also release any FFmpeg child reading
            # the original so cancellation never leaves network handles behind.
            killer = await asyncio.create_subprocess_exec('taskkill', '/PID', str(proc.pid), '/T', '/F',
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, **hidden_process_kwargs())
            try:
                await asyncio.wait_for(killer.wait(), 15)
            except asyncio.TimeoutError:
                killer.kill()
                await killer.wait()
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
    await proc.wait()


_gate = None


async def prepare_sony_viewing(original, folder, technical, on_progress=None):
    """Decode complete source once; retain original facts and check proxy alignment."""
    global _gate
    if _gate is None:
        _gate = asyncio.Semaphore(1)
    async with _gate:
        return await _prepare_sony_viewing(original, folder, technical, on_progress)


async def _prepare_sony_viewing(original, folder, technical, on_progress):
    dll, shared = await asyncio.to_thread(sony_runtime)
    folder = Path(folder).resolve()
    if folder.parent != Path(settings.upload_dir).resolve():
        raise ValueError('Native proxy cache must belong to this app')
    folder.mkdir(parents=True, exist_ok=True)
    target, progress = folder / 'sony-viewing.mp4', folder / 'sony-progress.json'
    fps = Fraction(technical.get('frame_rate_fraction') or '0')
    duration = technical.get('duration_seconds') or 0
    frames = technical.get('camera_frame_count') or round(duration * float(fps))
    if fps <= 0 or frames < 1 or abs(frames / float(fps) - duration) > 1 / float(fps):
        raise ValueError('Sony RAW source timing could not be verified')
    if on_progress:
        on_progress({'stage': 'preparing_proxy', 'message': 'Preparing Sony RAW proxy', 'percent': 0})
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise ValueError('FFmpeg is required to encode the Sony RAW viewing proxy')
    capture = technical.get('camera_metadata', {}).get('capture', {})
    arguments = ['--source', str(original), '--target', str(target), '--progress', str(progress),
                 '--dll', str(dll), '--shared', str(shared), '--ffmpeg', ffmpeg, '--fps', str(fps),
                 '--frames', str(frames), '--width', str(technical['width']), '--height', str(technical['height']),
                 '--black', str(capture.get('RawBlackCodeValue', 512)), '--white', str(capture.get('RawWhiteCodeValue', 5472))]
    env = os.environ.copy()
    env.pop('GOOGLE_API_KEY', None)
    if getattr(sys, 'frozen', False):
        command = [sys.executable, '--sony-raw-worker', *arguments]
    else:
        env['PYTHONPATH'] = str(Path(__file__).resolve().parents[2])
        command = [sys.executable, '-m', 'app.services.sony_raw_worker', *arguments]
    proc = await asyncio.create_subprocess_exec(*command, env=env,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, **hidden_process_kwargs())
    wait = asyncio.create_task(proc.wait())
    state, last = {}, None
    deadline = time.monotonic() + min(21600, max(300, duration * 15 + 120))
    published = False
    try:
        while not wait.done():
            if time.monotonic() > deadline:
                raise ValueError('Sony RAW proxy preparation timed out; retry when the source is reachable')
            try:
                state = json.loads(progress.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                pass
            if state != last and on_progress:
                on_progress(state)
                last = state
            await asyncio.wait({wait}, timeout=.4)
        try:
            state = json.loads(progress.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            pass
        if proc.returncode or state.get('stage') != 'complete' or state.get('frames') != frames:
            raise ValueError(state.get('message') or 'Sony RAW decoder failed; the original has been kept')
        proxy_facts = await probe_media(str(target))
        if (not target.stat().st_size or proxy_facts.get('codec') != 'h264' or
                abs(proxy_facts.get('duration_seconds', 0) - frames / float(fps)) > 1 / float(fps) + .005 or
                Fraction(proxy_facts.get('frame_rate_fraction') or '0') != fps or
                proxy_facts.get('frame_rate_verified_frames') != frames or
                proxy_facts.get('audio_stream_count') != technical.get('audio_stream_count') or
                bool(proxy_facts.get('has_audio')) != bool(technical.get('has_audio'))):
            raise ValueError('Sony RAW proxy failed its duration, frame-rate, video or audio checks')
        published = True
        if on_progress:
            on_progress({'stage': 'complete', 'message': 'Sony RAW proxy prepared', 'percent': 100})
        return target, {
            'linked': False, 'generated': True, 'decoder': 'sony_f55_raw', 'codec': 'h264',
            'width': proxy_facts['width'], 'height': proxy_facts['height'], 'frame_count': frames,
            'duration_seconds': proxy_facts['duration_seconds'], 'frame_rate_fraction': str(fps),
            'audio_stream_count': proxy_facts.get('audio_stream_count'),
            'color_calibrated': False, 'viewing_transform': 'Stable RAW black/white levels with a Rec.709-like display curve; no camera color matrix or LUT',
            'alignment': 'All consecutive source pictures decoded at original rational cadence; proxy duration, rate, frame count and audio track count checked',
        }
    finally:
        if proc.returncode is None:
            await stop_worker(proc)
        await wait
        progress.unlink(missing_ok=True)
        progress.with_suffix('.tmp').unlink(missing_ok=True)
        for name in ('sony-viewing.video.mp4', 'sony-viewing.partial.mp4', 'sony-viewing.ffmpeg.log'):
            (folder / name).unlink(missing_ok=True)
        if not published:
            target.unlink(missing_ok=True)
