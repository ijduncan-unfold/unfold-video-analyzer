"""Bounded, temporary Gemini uploads for long originals; never edit source media."""
import asyncio
import logging
import tempfile
import math
from pathlib import Path

from app.services.analysis_support import time_to_seconds
from app.services.file_manager import poll_until_active
from app.services.gemini_client import get_client
from app.services.job_store import get_job, update_job
from app.services.proxy_policy import proxy_decision
from app.utils.media_process import hidden_process_kwargs

logger = logging.getLogger(__name__)
SEGMENT_SECONDS = 600
LONG_VIDEO_SECONDS = 1800
MAX_SEGMENT_BYTES = 512 * 1024 * 1024


def needs_segments(job):
    if job.youtube_url or not job.local_path:
        return False
    duration = job.technical.get('duration_seconds')
    return duration is not None and time_to_seconds(duration) > 0 and proxy_decision(job)['proxy_used']


async def extract_segment(source, destination, start, end):
    """Accurate transcoding seek, preserving audio and a zero-based part timeline."""
    proc = await asyncio.create_subprocess_exec(
        'ffmpeg', '-nostdin', '-v', 'error', '-ss', str(start), '-i', str(source),
        '-t', str(end - start), '-map', '0:v:0', '-map', '0:a:0?',
        '-vf', 'scale=1280:720:force_original_aspect_ratio=decrease:force_divisible_by=2',
        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', '-threads', '2',
        '-maxrate', '4M', '-bufsize', '8M',
        '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart',
        '-y', str(destination), stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE, **hidden_process_kwargs())
    try:
        await asyncio.wait_for(proc.communicate(), timeout=1200)
    except BaseException:
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        await proc.wait()
        raise
    if proc.returncode or not destination.is_file() or not 0 < destination.stat().st_size <= MAX_SEGMENT_BYTES:
        raise ValueError('Could not create a bounded analysis part')


class SegmentedVideo:
    def __init__(self, job, api_key=None):
        self.job, self.api_key = job, api_key
        self.duration = time_to_seconds(job.technical['duration_seconds'])
        self.parts = {}
        self.uploaded = []
        self.failed_parts = set()
        self.source_identity = self.identity()

    def identity(self):
        stat = Path(self.job.local_path).stat()
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns

    def progress(self, stage, message, **details):
        latest = get_job(self.job.job_id)
        if latest:
            update_job(self.job.job_id, progress=message,
                       analysis_progress={**latest.analysis_progress, 'stage': stage, **details})

    async def get(self, start, end):
        start, end = time_to_seconds(start), time_to_seconds(end)
        index = int(start // SEGMENT_SECONDS)
        offset, stop = index * SEGMENT_SECONDS, min((index + 1) * SEGMENT_SECONDS, self.duration)
        if end <= start or end > stop + 0.001 or start >= self.duration:
            raise ValueError('Analysis interval crosses an upload part boundary')
        if self.identity() != self.source_identity:
            raise ValueError('Original media changed during analysis')
        if self.job.media_origin == 'external':
            from app.services.editor_bridge import ensure_source_unchanged
            ensure_source_unchanged(self.job)
        if index not in self.parts:
            if index in self.failed_parts:
                raise RuntimeError('This upload part failed; retry analysis to fill its missing coverage')
            try:
                await self.prepare(index, offset, stop)
            except Exception:
                self.failed_parts.add(index)
                raise
        return self.parts[index]

    async def prepare(self, index, offset, stop):
        total = math.ceil(self.duration / SEGMENT_SECONDS)
        self.progress('preparing_proxy', f'Preparing analysis proxy · part {index + 1} of {total}',
                      upload_part=index + 1, total_upload_parts=total)
        # Only this app-owned temporary proxy is removed. Original path is
        # never a cleanup target, and no full-film copy is created.
        with tempfile.TemporaryDirectory(prefix='video-analyzer-part-') as directory:
            path = Path(directory) / 'analysis.mp4'
            await extract_segment(self.job.local_path, path, offset, stop)
            if self.identity() != self.source_identity:
                raise ValueError('Original media changed while preparing an analysis part')
            self.progress('uploading', f'Uploading analysis proxy · part {index + 1} of {total}')
            client = get_client(self.api_key)
            remote = await asyncio.wait_for(client.aio.files.upload(
                file=path, config={'mime_type': 'video/mp4'}), timeout=300)
            self.uploaded.append(remote.name)
            self.progress('processing', f'Gemini is preparing part {index + 1} of {total}')
            active = await poll_until_active(remote.name, api_key=self.api_key)
            if not active.uri:
                raise ValueError('Gemini returned no usable part')
            self.parts[index] = (active.uri, 'video/mp4', float(offset))
        self.progress('indexing', 'Continuing section analysis')

    async def close(self):
        # Retain parts through the detailed pass, then release provider storage.
        if not self.uploaded:
            return
        client = get_client(self.api_key)
        for name in self.uploaded:
            try:
                await asyncio.wait_for(client.aio.files.delete(name=name), timeout=15)
            except Exception:
                logger.warning('Temporary provider part cleanup failed')
        self.uploaded.clear()
