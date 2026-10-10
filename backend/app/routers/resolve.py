"""Authenticated loopback API for the DaVinci Resolve workflow integration."""
import os
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from app.config import settings
from app.models.library import AnalyzeRequest
from app.routers.library import analyze_asset, require_job, search_shots
from app.services.editor_bridge import (
    DesktopSettings, EditorSource, editor_sources, editor_view, ensure_source_unchanged,
    project_assets, register_source, require_editor, save_desktop_settings,
)
from app.services.library_export import export_library

router = APIRouter(prefix='/api/editor/resolve', dependencies=[Depends(require_editor)])


def _resolve_job(job_id):
    job = require_job(job_id)
    if not editor_sources(job_id, host='resolve'):
        raise HTTPException(404, 'Asset is not registered with Resolve. Load the source from Resolve first.')
    ensure_source_unchanged(job)
    return job


@router.get('/health')
async def health(request: Request):
    import shutil
    return {'status': 'ok', 'protocol_version': 1, 'host': 'resolve',
            'instance_id': getattr(request.app.state, 'editor_instance_id', None),
            'google_configured': bool(settings.google_api_key),
            'google_api_key_configured': bool(settings.google_api_key),
            'desktop_mode': os.environ.get('VIDEO_ANALYZER_DESKTOP') == '1',
            'local_media': bool(shutil.which('ffmpeg') and shutil.which('ffprobe'))}


@router.post('/settings')
async def save_settings(body: DesktopSettings, request: Request):
    save_desktop_settings(body)
    return await health(request)


@router.post('/register')
async def register(body: EditorSource):
    return await register_source(body, host='resolve')


@router.get('/assets')
async def assets(project_id: str = Query(min_length=1, max_length=500)):
    result = project_assets(project_id, host='resolve')
    return {'assets': result, 'total': len(result)}


@router.get('/assets/{job_id}')
async def asset(job_id: str):
    return editor_view(_resolve_job(job_id), host='resolve')


@router.get('/assets/{job_id}/shots')
async def shots(job_id: str, q: str = Query(default='', max_length=500),
                limit: int = Query(default=500, ge=1, le=500)):
    _resolve_job(job_id)
    return await search_shots(q=q, job_id=job_id, limit=limit, offset=0,
                              project='', review_status='', tag='', rights_status='', collection='')


@router.post('/assets/{job_id}/analyze', status_code=202)
async def analyze(job_id: str, body: AnalyzeRequest):
    _resolve_job(job_id)
    return await analyze_asset(job_id, body, api_key=None)


@router.get('/assets/{job_id}/subtitles')
async def subtitles(job_id: str):
    job = _resolve_job(job_id)
    try:
        content, media_type, _ = export_library(job, 'srt')
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    name = quote(f'{job.filename.rsplit(".", 1)[0]}.srt')
    return Response(content=content, media_type=media_type,
                    headers={'Content-Disposition': f"attachment; filename*=UTF-8''{name}"})
