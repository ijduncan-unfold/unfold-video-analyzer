"""In-process background jobs with durable progress and explicit restart recovery."""
import asyncio
import json
import logging
from datetime import datetime, timezone
from fastapi import HTTPException
from app.config import settings
from app.services.analyzer import run_analysis
from app.services.file_manager import ensure_remote_file
from app.services.job_store import get_job, update_job

logger = logging.getLogger(__name__)
_tasks: dict[str, asyncio.Task] = {}
_gate: asyncio.Semaphore | None = None


def _stage(job_id, stage, **changes):
    job = get_job(job_id)
    if job is not None:
        update_job(job_id, analysis_progress={**job.analysis_progress, 'stage': stage,
                   'updated_at': datetime.now(timezone.utc).isoformat()}, **changes)


async def _run(job_id, request, api_key):
    global _gate
    if _gate is None:
        _gate = asyncio.Semaphore(max(1, settings.max_concurrent_analyses))
    try:
        async with _gate:
            job = get_job(job_id)
            if job is None:
                return
            # A source can change while this job is waiting for an analysis slot.
            if job.media_origin == 'external':
                from app.services.editor_bridge import ensure_source_unchanged
                ensure_source_unchanged(job)
            _stage(job_id, 'uploading', status='analyzing', error=None, warnings=[],
                   progress='Preparing video for analysis')
            await ensure_remote_file(job, api_key)
            job = get_job(job_id)
            async for event in run_analysis(job, request.fps, request.mode,
                    custom_prompt=request.custom_prompt.strip() or None, api_key=api_key):
                data = json.loads(event.get('data', '{}'))
                kind = event.get('event')
                # The analyzer owns section-level progress; do not overwrite it with
                # generic pass labels as soon as it yields a checkpoint event.
                if kind == 'analysis_complete':
                    update_job(job_id, progress='Analysis complete; ready for review')
                elif kind == 'error_event' and data.get('recoverable'):
                    latest = get_job(job_id)
                    warning = data.get('message', 'Partial analysis failure')
                    if latest and warning not in latest.warnings:
                        update_job(job_id, warnings=latest.warnings + [warning])
    except asyncio.CancelledError:
        _stage(job_id, 'cancelled', status='error', error='Analysis cancelled. Completed sections preserved.', progress='Cancelled')
        raise
    except Exception as exc:
        logger.warning('Analysis job failed (%s)', type(exc).__name__)
        # Avoid including provider exception payloads, which can contain URLs/credentials.
        message = 'Video preparation failed. Check the source file, provider access and quota, then retry.'
        if isinstance(exc, HTTPException) and exc.status_code == 409:
            message = 'The original source is offline or changed. Load the Premiere selection again before analyzing.'
        _stage(job_id, 'error', status='error', error=message, progress='Analysis failed; completed sections preserved')


def _finished(job_id, task):
    # A coroutine cancelled before its first step never reaches _run's handlers.
    # Only the registered task may clear/update this job, so an older callback
    # cannot erase a newer task created for the same asset.
    if _tasks.get(job_id) is not task:
        return
    _tasks.pop(job_id, None)
    if task.cancelled():
        job = get_job(job_id)
        if job and job.status in ('queued', 'analyzing', 'processing'):
            _stage(job_id, 'cancelled', status='error', error='Analysis cancelled. Completed sections preserved.', progress='Cancelled')
    elif task.exception() is not None:
        logger.warning('Analysis task ended unexpectedly (%s)', type(task.exception()).__name__)


def start_job(job_id, request, api_key):
    task = asyncio.create_task(_run(job_id, request, api_key))
    _tasks[job_id] = task
    task.add_done_callback(lambda completed: _finished(job_id, completed))


def cancel_job(job_id):
    task = _tasks.get(job_id)
    if task is None or task.done():
        return False
    return task.cancel()


async def shutdown_jobs():
    global _gate
    running = list(_tasks.values())
    for task in running:
        task.cancel()
    if running:
        await asyncio.gather(*running, return_exceptions=True)
    _gate = None
