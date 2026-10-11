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

BRAW_DLL_SHA256 = '71a1f9ff0efdf2f9396f26a94a5a5e6ab52fbde927d6d0ba81c8f527c66b54b5'
RED_BRIDGE_SHA256 = 'f8e9f0913a69e71525f8d7eb830d775059fa741e721a402a63734cc4e399377c'
RED_DLL_SHA256 = '3e1a32e5101369dbabec7333d14cbc7bbef64acbb80c2f2710a342688e2611ef'


def family_runtime(family, pins):
    """Resolve only bundled resources; pin ABI-critical binaries and all cache files."""
    if sys.platform != 'win32':
        raise ValueError(f'{family} native decoding currently requires Windows x64')
    configured = os.environ.get('VIDEO_ANALYZER_DECODER_DIR')
    root = Path(configured).resolve() if configured else None
    if root is None and getattr(sys, 'frozen', False):
        root = Path(sys.executable).resolve().parent.parent / 'camera-decoders'
    if root:
        for name, digest in pins.items():
            file = root / family / name
            if not file.is_file() or _sha(file) != digest:
                raise ValueError(f'The {family} decoder is missing or is an unverified version')
        return root / family / next(iter(pins)), root / 'shared'
    repo = Path(__file__).resolve().parents[3]
    archive = repo / 'portable' / 'VideoAnalyzer-windows-x64.zip'
    cache = Path(settings.database_path).resolve().parent / 'camera-decoders' / next(iter(pins.values()))
    complete = cache / 'complete.json'
    if complete.is_file():
        try:
            cached = json.loads(complete.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            cached = {}
        required = {f'{family}/{name}': digest for name, digest in pins.items()}
        if (isinstance(cached, dict) and all(cached.get(name) == digest for name, digest in required.items())
                and all((cache / name).resolve().is_relative_to(cache.resolve()) and
                        (cache / name).is_file() and _sha(cache / name) == digest for name, digest in cached.items())):
            return cache / family / next(iter(pins)), cache / 'shared'
    if not archive.is_file() or not zipfile.is_zipfile(archive):
        raise ValueError(f'Download the Windows portable bundle with git lfs pull to enable {family}')
    with zipfile.ZipFile(archive) as bundle:
        manifest = json.loads(bundle.read('manifest.json'))
        entries = {x['path'].replace('\\', '/'): x for x in manifest['files']}
        for name, digest in pins.items():
            if entries.get(f'camera-decoders/{family}/{name}', {}).get('sha256') != digest:
                raise ValueError(f'The bundled {family} decoder version is unverified')
        names = {name.replace('\\', '/'): name for name in bundle.namelist()}
        cached = {}
        for name, entry in entries.items():
            if not name.startswith((f'camera-decoders/{family}/', 'camera-decoders/shared/')):
                continue
            relative = Path(name).relative_to('camera-decoders')
            destination = (cache / relative).resolve()
            if not destination.is_relative_to(cache.resolve()) or entry['bytes'] > 256 * 1024 * 1024:
                raise ValueError('Invalid bundled decoder resource')
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(destination.name + '.' + uuid.uuid4().hex + '.tmp')
            try:
                with bundle.open(names[name]) as source, temporary.open('wb') as output:
                    shutil.copyfileobj(source, output)
                if _sha(temporary) != entry['sha256']:
                    raise ValueError('Bundled decoder checksum mismatch')
                temporary.replace(destination)
                cached[relative.as_posix()] = entry['sha256']
            finally:
                temporary.unlink(missing_ok=True)
        complete.write_text(json.dumps(cached), encoding='utf-8')
    return cache / family / next(iter(pins)), cache / 'shared'


def red_runtime():
    return family_runtime('red', {'VideoAnalyzerRED.dll': RED_BRIDGE_SHA256, 'REDR3D-x64.dll': RED_DLL_SHA256})


async def prepare_ffmpeg_viewing(original, folder, technical, on_progress=None):
    """Develop FFmpeg-supported camera codecs once, with complete audio and progress."""
    folder = Path(folder).resolve()
    if folder.parent != Path(settings.upload_dir).resolve():
        raise ValueError('Camera proxy cache must belong to this app')
    folder.mkdir(parents=True, exist_ok=True)
    target, partial = folder / 'camera-viewing.mp4', folder / 'camera-viewing.partial.mp4'
    duration = technical.get('duration_seconds') or 0
    if duration <= 0:
        raise ValueError('Camera source duration could not be measured')
    if on_progress:
        on_progress({'stage': 'preparing_proxy', 'message': 'Preparing camera viewing proxy', 'percent': 0})
    log = folder / 'camera-ffmpeg.log'
    published, proc = False, None
    try:
        with log.open('wb') as error:
            proc = await asyncio.create_subprocess_exec('ffmpeg', '-nostdin', '-v', 'error', '-i', str(original),
                '-map', '0:v:0', '-map', '0:a?', '-vf',
                'scale=1280:720:force_original_aspect_ratio=decrease:force_divisible_by=2',
                '-fps_mode', 'passthrough', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23',
                '-threads', '2', '-filter_threads', '2', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '128k',
                '-movflags', '+faststart', '-progress', 'pipe:1', '-nostats', '-y', str(partial),
                stdout=asyncio.subprocess.PIPE, stderr=error, **hidden_process_kwargs())
            async def read_progress():
                while line := await proc.stdout.readline():
                    key, _, raw = line.decode('utf-8', errors='replace').strip().partition('=')
                    if key == 'out_time_us' and on_progress:
                        try:
                            percent = min(99, max(0, int(raw) / 1_000_000 / duration * 100))
                        except ValueError:
                            continue
                        on_progress({'stage': 'preparing_proxy', 'message': 'Preparing camera viewing proxy', 'percent': round(percent, 1)})
                await proc.wait()
            await asyncio.wait_for(read_progress(), timeout=min(21600, max(300, duration * 15)))
        if proc.returncode or not partial.is_file() or not partial.stat().st_size:
            raise ValueError('Camera decoder could not prepare a viewing proxy; the original has been kept')
        proxy = await probe_media(str(partial))
        tolerance = 1 / max(1, technical.get('frame_rate') or 1) + .005
        if (proxy.get('codec') != 'h264' or abs(proxy.get('duration_seconds', 0) - duration) > tolerance or
            proxy.get('audio_stream_count') != technical.get('audio_stream_count', 0) or
            (technical.get('nominal_frame_rate_verified') and
             (Fraction(proxy.get('frame_rate_fraction') or '0') != Fraction(technical['frame_rate_fraction']) or
              proxy.get('frame_rate_verified_frames') != (technical.get('frame_rate_verified_frames') or technical.get('camera_frame_count'))))):
            raise ValueError('Camera viewing proxy failed its timing, cadence or audio checks')
        partial.replace(target)
        published = True
        if on_progress:
            on_progress({'stage': 'complete', 'message': 'Camera viewing proxy prepared', 'percent': 100})
        return target, {'linked': False, 'generated': True,
            'decoder': 'ffmpeg_prores_raw' if technical.get('codec') == 'prores_raw' else 'ffmpeg_camera',
            'codec': 'h264', 'width': proxy['width'], 'height': proxy['height'],
            'duration_seconds': proxy['duration_seconds'], 'frame_rate_fraction': proxy['frame_rate_fraction'],
            'audio_stream_count': proxy['audio_stream_count'], 'frame_count': proxy.get('frame_rate_verified_frames'),
            'color_calibrated': False, 'viewing_transform': 'FFmpeg decoded image; no automatic camera WB, color matrix or Log LUT',
            'alignment': 'Complete original processed without trimming or retiming; duration, available cadence and every audio stream checked'}
    except asyncio.TimeoutError:
        raise ValueError('Camera viewing proxy preparation timed out')
    finally:
        if proc and proc.returncode is None:
            await stop_worker(proc)
        partial.unlink(missing_ok=True)
        log.unlink(missing_ok=True)
        if not published:
            target.unlink(missing_ok=True)


def braw_runtime():
    return family_runtime('blackmagic', {'BlackmagicRawAPI.dll': BRAW_DLL_SHA256})


async def prepare_braw_viewing(original, folder, on_progress=None, family='braw'):
    """SDK inspects and decodes the original inside a disposable worker process."""
    global _gate
    if _gate is None:
        _gate = asyncio.Semaphore(1)
    async with _gate:
        label = 'RED RAW' if family == 'red' else 'Blackmagic RAW'
        dll, shared = await asyncio.to_thread(red_runtime if family == 'red' else braw_runtime)
        folder = Path(folder).resolve()
        if folder.parent != Path(settings.upload_dir).resolve():
            raise ValueError('Native proxy cache must belong to this app')
        folder.mkdir(parents=True, exist_ok=True)
        target, progress = folder / f'{family}-viewing.mp4', folder / f'{family}-progress.json'
        ffmpeg = shutil.which('ffmpeg')
        if not ffmpeg:
            raise ValueError(f'FFmpeg is required to encode the {label} viewing proxy')
        arguments = ['--source', str(original), '--target', str(target), '--progress', str(progress),
                     '--dll', str(dll), '--shared', str(shared), '--ffmpeg', ffmpeg]
        env = os.environ.copy()
        env.pop('GOOGLE_API_KEY', None)
        if getattr(sys, 'frozen', False):
            command = [sys.executable, f'--{family}-worker', *arguments]
        else:
            env['PYTHONPATH'] = str(Path(__file__).resolve().parents[2])
            command = [sys.executable, '-m', f'app.services.{family}_worker', *arguments]
        if on_progress:
            on_progress({'stage': 'preparing_proxy', 'message': f'Opening {label} decoder', 'percent': 0})
        proc = await asyncio.create_subprocess_exec(*command, env=env, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL, **hidden_process_kwargs())
        wait, state, last, published = asyncio.create_task(proc.wait()), {}, None, False
        deadline = time.monotonic() + 21600
        try:
            while not wait.done():
                if time.monotonic() > deadline:
                    raise ValueError(f'{label} proxy preparation timed out')
                try:
                    state = json.loads(progress.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    pass
                if state.get('stage') and state != last and on_progress:
                    on_progress({key: val for key, val in state.items() if key != 'technical'})
                    last = state
                await asyncio.wait({wait}, timeout=.4)
            try:
                state = json.loads(progress.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                pass
            if proc.returncode or state.get('stage') != 'complete':
                raise ValueError(state.get('message') or f'{label} decoder failed; the original has been kept')
            technical = state['technical']
            frames, fps = technical['camera_frame_count'], Fraction(technical['frame_rate_fraction'])
            proxy = await probe_media(str(target))
            if (not target.stat().st_size or proxy.get('codec') != 'h264' or
                abs(proxy.get('duration_seconds', 0) - frames / float(fps)) > 1 / float(fps) + .005 or
                Fraction(proxy.get('frame_rate_fraction') or '0') != fps or
                proxy.get('frame_rate_verified_frames') != frames or
                proxy.get('audio_stream_count') != technical['audio_stream_count']):
                raise ValueError(f'{label} proxy failed its timing, frame-count or audio checks')
            technical['viewing_proxy'] = {'linked': False, 'generated': True, 'decoder': 'red_raw' if family == 'red' else 'blackmagic_raw',
                'codec': 'h264', 'width': proxy['width'], 'height': proxy['height'], 'frame_count': frames,
                'duration_seconds': proxy['duration_seconds'], 'frame_rate_fraction': str(fps),
                'audio_stream_count': proxy['audio_stream_count'], 'color_calibrated': False,
                'viewing_transform': f'{label} SDK camera/sidecar processing defaults; reduced-resolution 8-bit viewing output',
                'alignment': 'Every source frame decoded at SDK cadence; complete frame count, duration, frame rate and audio verified'}
            published = True
            if on_progress:
                on_progress({'stage': 'complete', 'message': f'{label} proxy prepared', 'percent': 100})
            return target, technical
        finally:
            if proc.returncode is None:
                await stop_worker(proc)
            await wait
            for file in (progress, progress.with_suffix('.tmp'), target.with_suffix('.video.mp4'),
                         target.with_suffix('.partial.mp4'), target.with_suffix('.ffmpeg.log'), target.with_suffix('.wav')):
                file.unlink(missing_ok=True)
            if not published:
                target.unlink(missing_ok=True)


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
            if state.get('stage') and state != last and on_progress:
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
