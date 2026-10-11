"""Change only app-owned viewing media; preserve the original and analysis timing."""
import asyncio
import hashlib
import io
from pathlib import Path
from uuid import uuid4
import zipfile

import httpx
from fastapi import HTTPException

from app.config import settings
from app.services.editor_bridge import ensure_source_unchanged, source_fingerprint
from app.services.job_store import claim_viewing, get_job, update_job
from app.services.media_probe import create_poster, probe_media
from app.services.native_camera import prepare_ffmpeg_viewing, prepare_braw_viewing

DJI_URL = 'https://dl.djicdn.com/downloads/zenmuse+x7/20171017/D-Log+to+Rec709.zip'
DJI_SHA = '0a576b299d8c7b9310a41fcbcac983bfaaf626e8bc3d4acc6cc15fb33972dab8'
LABELS = {'source': 'As decoded', 'dji_zenmuse_dlog': 'DJI Zenmuse X5S/X7 D-Log → Rec.709',
          'red_rec709': 'RED SDK → Rec.709'}


async def dji_lut():
    cache = Path(settings.database_path).parent / 'display-luts' / 'dji-zenmuse-dlog.cube'
    if cache.is_file() and cache.stat().st_size <= 2 * 1024 * 1024 and hashlib.sha256(cache.read_bytes()).hexdigest() == DJI_SHA:
        return cache.read_bytes()
    async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
        async with client.stream('GET', DJI_URL) as response:
            response.raise_for_status()
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > 8 * 1024 * 1024:
                    raise ValueError('DJI LUT download exceeded its size limit')
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        item = archive.getinfo('D-Log to Rec709.cube')
        if item.file_size > 2 * 1024 * 1024:
            raise ValueError('Unexpected DJI LUT size')
        data = archive.read(item)
    if hashlib.sha256(data).hexdigest() != DJI_SHA:
        raise ValueError('DJI LUT verification failed; the previous preview is kept')
    cache.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache.with_name(uuid4().hex + '.tmp')
    try:
        temporary.write_bytes(data)
        temporary.replace(cache)
    finally:
        temporary.unlink(missing_ok=True)
    return data


async def set_display_color(job_id, mode):
    if mode not in LABELS:
        raise HTTPException(422, 'Choose an available display transform')
    current = get_job(job_id)
    if not current:
        raise HTTPException(404, 'Shot not found')
    if not current.local_path:
        raise HTTPException(422, 'Display color requires locally available media')
    if mode == 'red_rec709' and Path(current.local_path).suffix.lower() != '.r3d':
        raise HTTPException(422, 'The RED display transform requires an R3D original')
    folder = Path(settings.upload_dir).resolve() / job_id
    if folder.resolve().parent != Path(settings.upload_dir).resolve():
        raise HTTPException(422, 'Invalid viewing cache')
    folder.mkdir(parents=True, exist_ok=True)
    job = claim_viewing(job_id)
    if not job:
        raise HTTPException(409, 'Finish analysis or media preparation before changing display color')
    target, committed = None, False
    poster = folder / ('display-poster-' + uuid4().hex + '.jpg')
    try:
        ensure_source_unchanged(job)
        base = job.viewing_base or {'path': job.viewing_path, 'fingerprint': job.viewing_fingerprint,
            'proxy': job.technical.get('viewing_proxy', {}),
            'playback_proxy_required': job.technical.get('playback_proxy_required', False)}
        source = base['path'] or job.local_path
        source_before = source_fingerprint(source)[0]
        if base['path'] and source_fingerprint(source)[0] != base['fingerprint']:
            raise ValueError('The ungraded viewing copy changed; import the original again')
        technical = {**job.technical}
        if mode == 'source':
            target = Path(source)
            facts = base['proxy']
        else:
            name = 'display-' + uuid4().hex + '.mp4'
            def progress(value):
                update_job(job_id, progress=value.get('message') or 'Preparing display color',
                           analysis_progress={'stage': 'preparing_display', 'percent': value.get('percent')})
            if mode == 'red_rec709':
                target, decoded = await prepare_braw_viewing(Path(job.local_path), folder, progress,
                    family='red', target_name=name)
                facts = decoded['viewing_proxy']
            else:
                (folder / 'display-lut.cube').write_bytes(await dji_lut())
                source_facts = await probe_media(str(source))
                target, facts = await prepare_ffmpeg_viewing(Path(source), folder, source_facts,
                    progress, target_name=name, lut=True)
                facts['viewing_transform'] = LABELS[mode] + ' · official DJI display LUT'
                facts['lut_sha256'] = DJI_SHA
            facts['display_mode'] = mode
        ensure_source_unchanged(job)
        if source_fingerprint(source)[0] != source_before:
            raise ValueError('The source viewing media changed while preparing display color')
        await create_poster(str(target), str(poster), technical['duration_seconds'])
        if not poster.is_file() or not poster.stat().st_size:
            raise ValueError('The new display preview could not be verified')
        technical['viewing_proxy'] = facts
        technical['display_color'] = mode
        technical['display_revision'] = uuid4().hex
        technical['playback_proxy_required'] = base['playback_proxy_required'] if mode == 'source' else False
        warnings = [w for w in job.warnings if not w.startswith('Display color changed.')]
        if job.flash_result:
            warnings.append('Display color changed. Analyze again to refresh color descriptions and clip thumbnails; existing edits and dialogue are preserved.')
        path = base['path'] if mode == 'source' else str(target)
        fingerprint = source_fingerprint(path)[0] if path else ''
        result = update_job(job_id, viewing_path=path, viewing_fingerprint=fingerprint,
            viewing_base=base, technical=technical, status=job.status,
            progress=job.progress, analysis_progress=job.analysis_progress, warnings=warnings,
            file_id='', file_uri='', file_uploaded_at=None)
        committed = True
        poster.replace(folder / 'poster.jpg')
        return result
    except (ValueError, OSError, httpx.HTTPError, zipfile.BadZipFile, KeyError) as exc:
        raise HTTPException(422, str(exc))
    finally:
        (folder / 'display-lut.cube').unlink(missing_ok=True)
        poster.unlink(missing_ok=True)
        if not committed:
            if target and target.name.startswith('display-'):
                target.unlink(missing_ok=True)
            update_job(job_id, status=job.status, progress=job.progress, analysis_progress=job.analysis_progress)
