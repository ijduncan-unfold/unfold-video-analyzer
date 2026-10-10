import asyncio
import logging
import os
from pathlib import Path
from uuid import uuid4
from app.utils.media_process import hidden_process_kwargs

logger = logging.getLogger(__name__)


def _time_to_seconds(time_str: str) -> float:
    from app.services.analysis_support import time_to_seconds
    return time_to_seconds(time_str)


def _thumbnail_time(shot: dict) -> float:
    """Use the shot midpoint; retain valid starts for incomplete legacy records."""
    start = _time_to_seconds(shot.get("start_time", "0:00"))
    try:
        end = _time_to_seconds(shot.get("end_time"))
    except (TypeError, ValueError):
        return start
    return start + (end - start) / 2 if end > start else start


def _remove_frame(path: Path):
    try:
        path.unlink(missing_ok=True)
    except OSError as error:
        logger.warning("Could not remove unusable thumbnail (%s)", type(error).__name__)


async def extract_thumbnails(
    video_path: str, job_id: str, scenes: list[dict], upload_dir: str
) -> str:
    """Extract one midpoint thumbnail per shot from the video file.

    Returns the thumbnail directory path.
    """
    thumb_dir = os.path.join(upload_dir, job_id, "thumbs")
    os.makedirs(thumb_dir, exist_ok=True)

    tasks = []
    gate = asyncio.Semaphore(3)
    async def extract_bounded(seconds, output):
        async with gate:
            return await _extract_frame(video_path, seconds, output)
    for scene in scenes:
        for shot in scene.get("shots", []):
            shot_num = shot.get("shot_number", 0)
            out_path = os.path.join(thumb_dir, f"shot_{shot_num}.jpg")
            try:
                secs = _thumbnail_time(shot)
            except (TypeError, ValueError):
                _remove_frame(Path(out_path))
                logger.warning("Skipping thumbnail with an invalid source start time")
                continue
            tasks.append(extract_bounded(secs, out_path))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    ok = sum(1 for r in results if r is True)
    logger.info(f"Extracted {ok}/{len(tasks)} thumbnails for job {job_id}")
    return thumb_dir


async def _extract_frame(video_path: str, seconds: float, output_path: str) -> bool:
    target = Path(output_path)
    temporary = target.with_name(f"{target.stem}.{uuid4().hex}.tmp.jpg")
    published = False
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-ss", str(seconds),
            "-i", video_path,
            "-frames:v", "1",
            "-vf", "scale=640:-2",
            "-q:v", "3",
            "-y",
            str(temporary),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            **hidden_process_kwargs(),
        )
        try:
            await asyncio.wait_for(proc.wait(), timeout=45)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
            raise
        # FFmpeg may report success without producing a frame when seeking past EOF.
        if proc.returncode != 0 or not temporary.is_file() or temporary.stat().st_size == 0:
            return False
        temporary.replace(target)
        published = True
        return True
    except Exception as e:
        logger.warning("Failed to extract thumbnail (%s)", type(e).__name__)
        return False
    finally:
        _remove_frame(temporary)
        if not published:
            _remove_frame(target)
