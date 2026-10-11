import uuid
from pathlib import Path

import aiofiles
from fastapi import APIRouter, HTTPException, UploadFile

from app.config import settings
from app.services.job_store import Job, create_job
from app.services.media_probe import probe_media, create_poster
from app.utils.video_formats import get_mime_type

router = APIRouter()


@router.post('/api/upload')
async def upload(file: UploadFile):
    if not file.filename:
        raise HTTPException(400, 'No filename provided')
    filename = Path(file.filename.replace('\\', '/')).name
    mime_type = get_mime_type(filename)
    if not mime_type:
        raise HTTPException(400, 'Unsupported video format')
    if Path(filename).suffix.lower() in {'.braw', '.r3d', '.ari', '.arx', '.crm'}:
        raise HTTPException(422, 'Link RAW camera originals without copying. Add a full-length viewing proxy if a vendor decoder is needed.')
    job_id = str(uuid.uuid4())
    local_path = Path(settings.upload_dir) / f'{job_id}{Path(filename).suffix.lower()}'
    local_path.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    try:
        async with aiofiles.open(local_path, 'wb') as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.max_file_size_mb * 1024 * 1024:
                    raise HTTPException(413, f'File too large. Max {settings.max_file_size_mb}MB')
                await output.write(chunk)
        if not size:
            raise HTTPException(400, 'Video file is empty')
        try:
            technical = await probe_media(str(local_path))
        except FileNotFoundError:
            raise HTTPException(503, 'Install ffmpeg and ffprobe to import local media')
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        from app.services.camera_media import browser_playable, decode_check, decoder_guidance
        if not browser_playable(technical, filename):
            if not await decode_check(local_path):
                raise HTTPException(422, decoder_guidance(filename, technical.get('codec', '')))
            technical['playback_proxy_required'] = True
        await create_poster(str(local_path), str(Path(settings.upload_dir) / job_id / 'poster.jpg'), technical['duration_seconds'])
        job = create_job(Job(job_id=job_id, file_id='', file_uri='', mime_type=mime_type,
            filename=filename, size_bytes=size, local_path=str(local_path), technical=technical,
            metadata={'title': Path(filename).stem}))
    except BaseException:
        local_path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    return {'job_id': job.job_id, 'file_id': '', 'filename': job.filename,
            'size_bytes': size, 'mime_type': mime_type, 'status': job.status}
