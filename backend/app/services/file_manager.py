import asyncio
import time
from pathlib import Path

from google.genai import types

from app.services.gemini_client import get_client


async def upload_video(file_path: str, mime_type: str, api_key: str | None = None):
    """Upload a video to Google's File API and return the file object."""
    client = get_client(api_key)
    uploaded = await asyncio.to_thread(
        client.files.upload, file=Path(file_path), config={"mime_type": mime_type}
    )
    return uploaded


async def poll_until_active(file_name: str, timeout: int = 300, interval: int = 5, api_key: str | None = None):
    """Poll the File API until the file reaches ACTIVE state."""
    client = get_client(api_key)
    start = time.time()

    while True:
        file_obj = await asyncio.to_thread(client.files.get, name=file_name)
        if file_obj.state == "ACTIVE":
            return file_obj
        if file_obj.state == "FAILED":
            raise RuntimeError(f"File processing failed: {file_name}")
        if time.time() - start > timeout:
            raise TimeoutError(
                f"File {file_name} did not become ACTIVE within {timeout}s"
            )
        await asyncio.sleep(interval)


async def delete_file(file_name: str):
    """Delete a file from Google's File API."""
    client = get_client()
    await asyncio.to_thread(client.files.delete, name=file_name)

async def ensure_remote_file(job, api_key: str | None = None):
    """Refresh expiring Google uploads only when analysis is requested."""
    from datetime import datetime, timezone
    from app.services.job_store import update_job
    if job.youtube_url:
        return job
    from app.services.camera_media import viewing_media
    from app.utils.video_formats import get_mime_type
    media = viewing_media(job)
    if not media or not Path(media).is_file():
        raise ValueError('The original video is no longer available. Import it again.')
    client = get_client(api_key)
    if job.file_id:
        try:
            remote = await asyncio.to_thread(client.files.get, name=job.file_id)
            if str(remote.state) in ('ACTIVE', 'FileState.ACTIVE'):
                job.file_uri = remote.uri
                return job
        except Exception:
            pass
    update_job(job.job_id, progress='Uploading video to Gemini',
               analysis_progress={**job.analysis_progress, 'stage': 'uploading',
                                  'updated_at': datetime.now(timezone.utc).isoformat()})
    uploaded = await upload_video(media, get_mime_type(media) or job.mime_type, api_key=api_key)
    update_job(job.job_id, progress='Gemini is preparing the uploaded video')
    try:
        active = await poll_until_active(uploaded.name, api_key=api_key)
    except BaseException:
        try:
            await asyncio.to_thread(client.files.delete, name=uploaded.name)
        except Exception:
            pass
        raise
    job.file_id, job.file_uri = active.name, active.uri
    job.file_uploaded_at = datetime.now(timezone.utc).isoformat()
    update_job(job.job_id, file_id=job.file_id, file_uri=job.file_uri, file_uploaded_at=job.file_uploaded_at)
    return job
