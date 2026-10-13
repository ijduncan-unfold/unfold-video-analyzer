"""Authenticated loopback API for the Premiere UXP companion panel."""
import os
import asyncio
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from app.config import settings
from app.models.library import AnalyzeRequest
from app.routers.library import analyze_asset, require_job, search_shots
from app.services.editor_bridge import (
    PremiereSource, editor_view, ensure_source_unchanged, project_assets,
    register_source, require_editor, DesktopSettings, save_desktop_settings, saved_editor_view,
)
from app.services.library_export import export_library
from app.services.editor_metadata import editor_metadata

router = APIRouter(prefix='/api/editor/premiere', dependencies=[Depends(require_editor)])


@router.get('/selections')
async def saved_selections():
    from app.services.premiere_selections import selections
    return {'selections': await asyncio.to_thread(selections)}


@router.get('/selections/{selection_id}/plan')
async def selection_plan(selection_id: str):
    from app.services.premiere_selections import import_plan
    return await asyncio.to_thread(import_plan, selection_id)


@router.get('/selections/{selection_id}/items/{job_id}')
async def selection_metadata(selection_id: str, job_id: str):
    from app.services.premiere_selections import import_metadata
    return await asyncio.to_thread(import_metadata, selection_id, job_id)


@router.get('/health')
async def health():
    import shutil
    return {'status': 'ok', 'protocol_version': 1, 'host': 'premiere',
            'google_configured': bool(settings.google_api_key),
            'google_api_key_configured': bool(settings.google_api_key),
            'desktop_mode': os.environ.get('VIDEO_ANALYZER_DESKTOP') == '1',
            'local_media': bool(shutil.which('ffmpeg') and shutil.which('ffprobe'))}


@router.post('/settings')
async def save_settings(body: DesktopSettings):
    save_desktop_settings(body)
    return await health()


@router.post('/register')
async def register(body: PremiereSource):
    return await register_source(body)


@router.get('/assets')
async def assets(project_id: str = Query(min_length=1, max_length=500), include_saved: bool = False):
    result = project_assets(project_id, include_saved=include_saved)
    return {'assets': result, 'total': len(result)}


@router.get('/assets/{job_id}')
async def asset(job_id: str, saved: bool = False):
    job = require_job(job_id)
    if saved:
        return saved_editor_view(job)
    ensure_source_unchanged(job)
    return editor_view(job)


@router.get('/assets/{job_id}/shots')
async def shots(job_id: str, q: str = Query(default='', max_length=500),
                limit: int = Query(default=500, ge=1, le=500), saved: bool = False):
    job = require_job(job_id)
    if not saved:
        ensure_source_unchanged(job)
    return await search_shots(q=q, job_id=job_id, limit=limit, offset=0,
                              project='', review_status='', tag='', rights_status='', collection='',
                              character='', franchise='', identification='')


@router.post('/assets/{job_id}/analyze', status_code=202)
async def analyze(job_id: str, body: AnalyzeRequest):
    return await analyze_asset(job_id, body, api_key=None)


@router.get('/assets/{job_id}/subtitles')
async def subtitles(job_id: str):
    job = require_job(job_id)
    ensure_source_unchanged(job)
    try:
        content, media_type, _ = export_library(job, 'srt')
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    name = quote(f'{job.filename.rsplit(".", 1)[0]}.srt')
    return Response(content=content, media_type=media_type,
                    headers={'Content-Disposition': f"attachment; filename*=UTF-8''{name}"})


@router.get('/assets/{job_id}/metadata')
async def metadata_package(job_id: str, analysis_session: str = Query(default='', max_length=300)):
    job = require_job(job_id)
    ensure_source_unchanged(job)
    if str(job.analysis_config.get('started_at') or '') != analysis_session:
        raise HTTPException(409, 'Analysis changed. Refresh this clip before syncing metadata.')
    return editor_metadata(job, include_record=True)
