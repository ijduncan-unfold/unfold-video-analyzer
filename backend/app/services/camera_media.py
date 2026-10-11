"""Link camera originals read-only; decode a viewing copy without changing edit identity."""
import asyncio
import math
import uuid
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

from fastapi import HTTPException

from app.config import settings
from app.services.editor_bridge import ensure_source_unchanged, source_fingerprint, _check_interpreted_rate
from app.services.job_store import Job, create_job, list_jobs
from app.services.media_probe import probe_media, create_poster, _stop_process
from app.utils.media_process import hidden_process_kwargs
from app.utils.video_formats import get_mime_type

_preview_locks = {}
_link_lock = asyncio.Lock()


def viewing_media(job):
    ensure_source_unchanged(job)
    return job.viewing_path or job.local_path


async def browser_media(job):
    source = viewing_media(job)
    if not job.viewing_path and not job.technical.get('playback_proxy_required'):
        return source, job.mime_type
    if browser_playable(job.technical.get('viewing_proxy') or job.technical, source):
        return source, get_mime_type(source)
    # A small, app-owned playback proxy; original and user-provided proxies are read-only.
    folder = Path(settings.upload_dir) / job.job_id
    if folder.resolve().parent != Path(settings.upload_dir).resolve():
        raise ValueError('Invalid asset cache directory')
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / 'playback.mp4'
    async with _preview_locks.setdefault(job.job_id, asyncio.Lock()):
        if not target.is_file():
            temporary = folder / f'playback-{uuid.uuid4().hex}.mp4'
            proc = await asyncio.create_subprocess_exec('ffmpeg', '-nostdin', '-v', 'error', '-i', source,
                '-map', '0:v:0', '-map', '0:a:0?', '-vf', 'scale=1280:720:force_original_aspect_ratio=decrease:force_divisible_by=2',
                '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', '-threads', '2', '-maxrate', '4M', '-bufsize', '8M',
                '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart',
                '-y', str(temporary), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
                **hidden_process_kwargs())
            try:
                await asyncio.wait_for(proc.communicate(), timeout=1200)
                if proc.returncode or not temporary.is_file() or not temporary.stat().st_size:
                    raise ValueError('Playback proxy could not be prepared. Link a decodable full-length MP4 viewing proxy.')
                ensure_source_unchanged(job)
                temporary.replace(target)
            finally:
                if proc.returncode is None:
                    await _stop_process(proc)
                temporary.unlink(missing_ok=True)
    return str(target), 'video/mp4'


def sony_sidecar(path, technical):
    """Read only the bounded, adjacent Sony sidecar. Never follow XML references."""
    sidecar = path.with_name(path.stem + 'M01.xml')
    if not sidecar.is_file() or sidecar.stat().st_size > 256 * 1024:
        return technical
    try:
        text = sidecar.read_text(encoding='utf-8-sig')
        if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
            return technical
        root = ET.fromstring(text)
        frame, layout = root.find('.//{*}VideoFrame'), root.find('.//{*}VideoLayout')
        if frame is None or layout is None:
            return technical
        camera, device = root.find('.//{*}Camera'), root.find('.//{*}Device')
        result = {**technical, 'camera_metadata': {
            'manufacturer': camera.get('manufacturer') if camera is not None else None,
            'camera_model': camera.get('modelName') if camera is not None else None,
            'recorder_model': device.get('modelName') if device is not None else None,
            'recording_format': frame.get('videoCodec'),
            'capture_fps': frame.get('captureFps'), 'format_fps': frame.get('formatFps'),
            'metadata_source': 'Adjacent Sony recording XML',
        }}
        if not result.get('width'):
            result['width'] = int(layout.get('pixel'))
        if not result.get('height'):
            result['height'] = int(layout.get('numOfVerticalLine'))
        if not result.get('codec'):
            result['codec'] = frame.get('videoCodec', '')
        # Prefer an independently probed stream rate, never round Sony's 23.98 label.
        try:
            rate = Fraction(result.get('frame_rate_fraction') or '0/1')
        except (ValueError, ZeroDivisionError):
            rate = Fraction(0)
        if rate <= 0:
            rate = Fraction(result.get('nominal_frame_rate_fraction') or '0/1')
        if rate > 0:
            result['frame_rate'], result['frame_rate_fraction'] = float(rate), str(rate)
        capture = {item.get('name'): item.get('value') for item in root.findall('.//{*}AcquisitionRecord/{*}Group/{*}Item')
                   if item.get('name') in {'ISOSensitivity', 'WhiteBalance', 'CaptureGammaEquation', 'MonitoringBaseCurve', 'ExposureIndexOfPhotoMeter', 'LensAttributes', 'RawBlackCodeValue', 'RawGrayCodeValue', 'RawWhiteCodeValue'}}
        result['camera_metadata']['capture'] = capture
        duration = root.find('.//{*}Duration')
        if duration is not None:
            count = int(duration.get('value', '0'))
            if 0 < count <= 1_000_000:
                result['camera_frame_count'] = count
        return result
    except (OSError, ValueError, TypeError, ET.ParseError, ZeroDivisionError):
        return technical


async def decode_check(path):
    """A container probe alone cannot prove the video decoder works."""
    proc = await asyncio.create_subprocess_exec('ffmpeg', '-nostdin', '-v', 'error', '-i', str(path),
        '-map', '0:v:0', '-frames:v', '1', '-f', 'framemd5', '-',
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **hidden_process_kwargs())
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
    except asyncio.TimeoutError:
        await _stop_process(proc)
        return False
    except asyncio.CancelledError:
        await _stop_process(proc)
        raise
    return proc.returncode == 0 and any(line.strip() and not line.startswith(b'#') for line in stdout.splitlines())


def browser_playable(technical, path):
    return Path(path).suffix.lower() in {'.mp4', '.m4v', '.mov', '.webm'} and technical.get('codec') in {'h264', 'vp8', 'vp9', 'av1'}


def decoder_guidance(filename, codec=''):
    kind = Path(filename).suffix.lower()
    family = ('Sony RAW/X-OCN' if str(codec).upper().startswith(('F55RAW', 'F5RAW', 'XOCN', 'X-OCN')) else
              'the RAW/unsupported codec in this MXF' if kind == '.mxf' and not codec else
              'Blackmagic RAW' if kind == '.braw' else 'RED RAW' if kind == '.r3d' else
              'ARRIRAW' if kind in {'.ari', '.arx'} else 'Canon Cinema RAW' if kind == '.crm' else codec or 'this camera codec')
    return (f'The installed FFmpeg cannot decode {family}. Link a full-length viewing proxy exported '
            'by Resolve or the camera vendor tool. Preserve the original frame rate, duration, '
            'start timecode, framing and audio; do not trim or retime it. The original will remain the edit source.')


async def register_camera_source(media_path, proxy_path='', aligned=False, source_fps=None, on_progress=None):
    async with _link_lock:
        return await _register_camera_source(media_path, proxy_path, aligned, source_fps, on_progress)


async def _register_camera_source(media_path, proxy_path, aligned, source_fps, on_progress=None):
    original = Path(media_path)
    if not original.is_absolute() or not get_mime_type(original.name):
        raise HTTPException(422, 'Choose an absolute path to recognized video or camera media.')
    try:
        from app.services.camera_identity import recording_files
        original, companion_paths = await asyncio.to_thread(recording_files, original)
        companions = [{'path': str(path.resolve()), 'fingerprint': source_fingerprint(path)[0]} for path in companion_paths]
        fingerprint, original, size = source_fingerprint(original)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except OSError:
        raise HTTPException(422, 'The original is offline, inaccessible, or empty.')
    proxy = Path(proxy_path) if proxy_path else None
    if proxy and (not proxy.is_absolute() or not get_mime_type(proxy.name)):
        raise HTTPException(422, 'Choose an absolute path to the full-length viewing proxy.')
    if proxy and not aligned:
        raise HTTPException(422, 'Confirm that the viewing proxy is the same complete clip without trimming or retiming.')
    # Generated media is reused across imports and restarts after both identity
    # checks. The original is never copied, and another decode is unnecessary.
    if not proxy:
        for existing in list_jobs():
            if (existing.media_origin == 'external' and existing.source_fingerprint == fingerprint
                    and existing.technical.get('viewing_proxy', {}).get('generated')
                    and existing.status != 'deleting'):
                if existing.camera_companions is not None and existing.camera_companions != companions:
                    continue
                ensure_source_unchanged(existing)
                _check_interpreted_rate(source_fps, existing.technical)
                return existing
    if on_progress:
        on_progress({'stage': 'inspecting', 'message': 'Reading original media'})
    try:
        technical = await probe_media(str(original))
    except ValueError:
        technical = {}
    except FileNotFoundError:
        raise HTTPException(503, 'FFmpeg and ffprobe are required to import camera media.')
    technical = sony_sidecar(original, technical)
    if technical.get('duration_seconds') and technical.get('frame_rate'):
        _check_interpreted_rate(source_fps, technical)
    viewing_fingerprint = ''
    ident = str(uuid.uuid4())
    generated = False
    if proxy:
        try:
            viewing_fingerprint, proxy, _ = source_fingerprint(proxy)
            if proxy == original:
                raise ValueError('Choose a separate viewing proxy')
            proxy_technical = await probe_media(str(proxy))
        except (OSError, ValueError) as exc:
            raise HTTPException(422, str(exc))
        if not await decode_check(proxy):
            raise HTTPException(422, 'The viewing proxy cannot be decoded. Use H.264 MP4 or ProRes MOV.')
        for key, tolerance in [('duration_seconds', 1 / max(1, technical.get('frame_rate', 1))), ('frame_rate', .001)]:
            if technical.get(key) and abs(technical[key] - proxy_technical.get(key, 0)) > tolerance + 1e-6:
                raise HTTPException(422, 'The viewing proxy duration or frame rate does not match the original. Export the complete clip without retiming.')
        if technical.get('source_timecode') and proxy_technical.get('source_timecode') and technical['source_timecode'] != proxy_technical['source_timecode']:
            raise HTTPException(422, 'The viewing proxy starts at a different source timecode.')
        if all((technical.get(key) or 0) > 0 and (proxy_technical.get(key) or 0) > 0 for key in ('width', 'height')):
            if abs(technical['width'] / technical['height'] - proxy_technical['width'] / proxy_technical['height']) > .005:
                raise HTTPException(422, 'The viewing proxy aspect ratio differs from the original. Preserve framing without cropping or letterboxing.')
        if not technical.get('duration_seconds') or not technical.get('frame_rate'):
            # Useful for search and analysis, but do not pretend proxy cadence proves RAW source timing.
            technical = {**proxy_technical, 'width': None, 'height': None, 'codec': 'vendor_raw',
                         'source_timecode': None, 'nominal_frame_rate_verified': False,
                         'technical_basis': 'User-confirmed full-length viewing proxy; original timing and resolution unverified'}
        else:
            technical['technical_basis'] = 'Original inspection and adjacent recording metadata'
        technical['viewing_proxy'] = {'linked': True, 'codec': proxy_technical['codec'],
                                     'width': proxy_technical['width'], 'height': proxy_technical['height'],
                                     'alignment': 'User confirmed complete clip; available duration, rate and timecode checked'}
        source = proxy
    else:
        if original.suffix.lower() in {'.braw', '.r3d'}:
            from app.services.native_camera import prepare_braw_viewing
            try:
                family = 'red' if original.suffix.lower() == '.r3d' else 'braw'
                proxy, technical = await prepare_braw_viewing(original, Path(settings.upload_dir) / ident, on_progress, family=family)
                viewing_fingerprint, proxy, _ = source_fingerprint(proxy)
            except (ValueError, OSError) as exc:
                raise HTTPException(422, str(exc))
            generated = True
        elif not technical or not await decode_check(original):
            from app.services.native_camera import supports_sony_f55, prepare_sony_viewing
            if (original.suffix.lower() in {'.ari', '.arx'} or 'arri' in str(technical.get('codec', '')).lower()
                    or 'arri' in str(technical.get('camera_manufacturer', '')).lower()):
                from app.services.arri_camera import prepare_arri_viewing
                try:
                    proxy, technical = await prepare_arri_viewing(original, Path(settings.upload_dir) / ident, on_progress)
                    viewing_fingerprint, proxy, _ = source_fingerprint(proxy)
                except (ValueError, OSError, KeyError) as exc:
                    raise HTTPException(422, str(exc))
                generated = True
            elif not supports_sony_f55(original, technical):
                raise HTTPException(422, decoder_guidance(original.name, technical.get('codec', '')))
            else:
                _check_interpreted_rate(source_fps, technical)
                try:
                    proxy, generated_facts = await prepare_sony_viewing(original,
                        Path(settings.upload_dir) / ident, technical, on_progress)
                    viewing_fingerprint, proxy, _ = source_fingerprint(proxy)
                except (ValueError, OSError) as exc:
                    raise HTTPException(422, str(exc))
                technical['viewing_proxy'] = generated_facts
                technical['technical_basis'] = 'Original inspection and Sony recording metadata; SDK-developed viewing proxy'
                generated = True
        elif not browser_playable(technical, original):
            from app.services.native_camera import prepare_ffmpeg_viewing
            try:
                proxy, generated_facts = await prepare_ffmpeg_viewing(original, Path(settings.upload_dir) / ident, technical, on_progress)
                viewing_fingerprint, proxy, _ = source_fingerprint(proxy)
            except (ValueError, OSError) as exc:
                raise HTTPException(422, str(exc))
            technical['viewing_proxy'] = generated_facts
            generated = True
        source = proxy or original
    if not math.isfinite(technical.get('duration_seconds', 0)) or technical.get('duration_seconds', 0) <= 0:
        raise HTTPException(422, 'A positive measured duration is required.')
    try:
        _check_interpreted_rate(source_fps, technical)
    except HTTPException:
        if generated and proxy:
            Path(proxy).unlink(missing_ok=True)
        raise
    technical['playback_proxy_required'] = not browser_playable(technical.get('viewing_proxy') or technical, source)
    # Relinking the same unchanged pair is idempotent; never duplicate a camera original.
    for job in list_jobs():
        if (job.media_origin == 'external' and job.source_fingerprint == fingerprint
                and job.viewing_fingerprint == viewing_fingerprint and job.status != 'deleting'):
            return job
    job = Job(job_id=ident, file_id='', file_uri='', mime_type=get_mime_type(original.name),
              filename=original.name, size_bytes=size, local_path=str(original), technical=technical,
              metadata={'title': original.stem[:250]}, media_origin='external', source_fingerprint=fingerprint,
              viewing_path=str(proxy) if proxy else '', viewing_fingerprint=viewing_fingerprint,
              camera_companions=companions)
    try:
        await create_poster(str(source), str(Path(settings.upload_dir) / ident / 'poster.jpg'), technical['duration_seconds'])
        ensure_source_unchanged(job)
    except BaseException:
        if generated:
            # Only this import's derived outputs; never an external original.
            Path(proxy).unlink(missing_ok=True)
            (Path(settings.upload_dir) / ident / 'poster.jpg').unlink(missing_ok=True)
        raise
    if generated:
        label = {'blackmagic_raw': 'Blackmagic RAW', 'red_raw': 'RED RAW', 'sony_f55_raw': 'Sony RAW',
                 'arri_raw': 'ARRI', 'ffmpeg_prores_raw': 'ProRes RAW'}.get(technical.get('viewing_proxy', {}).get('decoder'), 'Camera media')
        transform = ' with an approximate display transform' if label == 'Sony RAW' else ''
        job.warnings.append(f'{label} analysis uses an automatically generated viewing proxy{transform}. Color results describe its viewing transform; original RAW media is unchanged.')
    elif proxy:
        job.warnings.append('Analysis uses a linked viewing proxy. Color results describe that proxy and its viewing transform. Verify proxy alignment before editing.')
    return create_job(job)
