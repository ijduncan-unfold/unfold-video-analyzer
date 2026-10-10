import json
import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from app.config import settings
from app.dependencies import get_api_key
from app.models.rough_cut import RoughCutEdit, RoughCutRequest
from app.routers.library import require_job
from app.services import rough_cut
from app.services.job_store import claim_rough_cut, update_job
from app.services.library_export import source_filename
from app.services.nle_export import export_fcpxml

router = APIRouter(prefix='/api/library')


@router.get('/{job_id}/rough-cut')
async def read_cut(job_id: str):
    return require_job(job_id).rough_cut


@router.post('/{job_id}/rough-cut', status_code=202)
async def create_cut(job_id: str, body: RoughCutRequest, api_key: str | None = Depends(get_api_key)):
    job = require_job(job_id)
    if not (api_key or settings.google_api_key):
        raise HTTPException(400, 'Connect Gemini in Settings to generate a rough cut')
    try:
        draft = rough_cut.new_draft(job, body)
    except (OSError, ValueError):
        raise HTTPException(400, 'Rough cuts require an available local video with audio and a measured duration')
    if not claim_rough_cut(job_id, draft):
        raise HTTPException(409, 'This video is busy. Finish or cancel its current analysis first.')
    rough_cut.start(job_id, api_key)
    return draft


@router.post('/{job_id}/rough-cut/cancel')
async def cancel_cut(job_id: str):
    require_job(job_id)
    if not rough_cut.cancel(job_id):
        raise HTTPException(409, 'There is no active rough cut to cancel')
    return {'status': 'cancelling'}


@router.patch('/{job_id}/rough-cut')
async def edit_cut(job_id: str, body: RoughCutEdit):
    job = require_job(job_id)
    try:
        draft = rough_cut.validate_current(job, body.revision)
        clips = rough_cut.validate_clips(draft, body.clips)
    except (OSError, ValueError) as exc:
        raise HTTPException(409, str(exc) if isinstance(exc, ValueError) else 'Original recording is unavailable')
    # This local single-worker API mutates synchronously, without yielding between revision check and save.
    draft = {**draft, 'revision': str(uuid.uuid4()), 'clips': clips, 'review_status': 'edited',
             'duration_seconds': round(sum(c['end_seconds'] - c['start_seconds'] for c in clips), 3)}
    update_job(job_id, rough_cut=draft)
    return draft


@router.get('/{job_id}/rough-cut/export')
async def export_cut(job_id: str, revision: str = Query(max_length=100),
                     format: str = Query(default='fcpxml', pattern='^(fcpxml|json)$')):
    job = require_job(job_id)
    try:
        draft = rough_cut.validate_current(job, revision)
        if format == 'fcpxml':
            content = export_fcpxml(rough_cut.export_data(job, draft), preserve_order=True,
                                   title=source_filename(job.filename) + ' Rough Cut')
            media_type = 'application/xml'
        else:
            content, media_type = json.dumps(draft, ensure_ascii=False, indent=2), 'application/json'
    except (OSError, ValueError) as exc:
        raise HTTPException(409, str(exc) if isinstance(exc, ValueError) else 'Original recording is unavailable')
    filename = source_filename(job.filename).rsplit('.', 1)[0] + '_rough_cut.' + format
    return Response(content, media_type=media_type,
                    headers={'Content-Disposition': f"attachment; filename*=UTF-8''{quote(filename, safe='')}"})
