from pathlib import Path
import asyncio
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response

from app.config import settings
from app.dependencies import get_api_key
from app.models.library import AssetMetadata, ShotAnnotation, AnalyzeRequest, FolderName, FolderMove
from app.services.job_store import get_job, list_jobs, update_job, claim_analysis, cut_is_active
from app.services.job_runner import start_job, cancel_job
from app.services.library_service import (
    asset_view, shot_views, search_score, match_shot, public_search_metadata, public_transcript_segments,
)

router = APIRouter(prefix='/api/library')
_poster_locks = {}


def require_job(job_id):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, 'Asset not found')
    return job


def _filtered(jobs, project='', review_status='', tag='', rights_status='', collection='', folder_id=''):
    for job in jobs:
        if folder_id and job.folder_id != (None if folder_id == 'unfiled' else folder_id):
            continue
        meta = AssetMetadata(**job.metadata)
        if project and meta.project != project:
            continue
        if review_status and meta.review_status != review_status:
            continue
        if rights_status and meta.rights_status != rights_status:
            continue
        if tag and tag.casefold() not in {v.casefold() for v in meta.tags}:
            continue
        if collection and collection not in meta.collections:
            continue
        yield job


@router.get('')
async def library(q: str = Query(default='', max_length=500), project: str = '', review_status: str = '',
                  tag: str = '', rights_status: str = '', collection: str = '', folder_id: str = '',
                  sort: str = Query(default='relevance', pattern='^(name|newest|relevance)$'),
                  limit: int = Query(default=200, ge=1, le=500), offset: int = Query(default=0, ge=0)):
    jobs = list_jobs()
    matched = []
    for job in _filtered(jobs, project, review_status, tag, rights_status, collection, folder_id):
        public = asset_view(job)
        score = search_score(q, [job.filename, public['metadata'], public['technical'], job.summary, job.flash_result,
                                 job.deep_results, job.shot_annotations,
                                 public_search_metadata(job.custom_result), public_transcript_segments(job)])
        if score >= 0:
            if q:
                matching_shot = next((match for shot in shot_views(job)
                                      if (match := match_shot(q, shot))[0] >= 0), None)
                public['match_context'] = matching_shot[1]['match_context'] if matching_shot else 'Matched asset metadata'
                public['match_sources'] = matching_shot[1]['match_sources'] if matching_shot else []
            matched.append((score, public))
    if sort == 'name':
        matched.sort(key=lambda item: item[1]['metadata']['title'].casefold())
    elif sort == 'newest':
        matched.sort(key=lambda item: item[1]['created_at'], reverse=True)
    else:
        matched.sort(key=lambda item: item[0], reverse=True)
    return {'assets': [v for _, v in matched[offset:offset + limit]], 'total': len(matched),
        'active_jobs': sum(j.status in ('queued', 'analyzing', 'processing') or cut_is_active(j) for j in jobs),
        'facets': {'projects': sorted({j.metadata.get('project') for j in jobs if j.metadata.get('project')}),
                   'tags': sorted({tag for j in jobs for tag in j.metadata.get('tags', [])}),
                   'collections': sorted({v for j in jobs for v in j.metadata.get('collections', [])})},
        'stats': {'assets': len(jobs), 'shots': sum(asset_view(j)['shot_count'] for j in jobs),
                  'reviewed': sum(j.metadata.get('review_status') == 'reviewed' for j in jobs),
                  'duration_seconds': sum(j.technical.get('duration_seconds', 0) for j in jobs)}}


@router.get('/search/shots')
async def search_shots(q: str = Query(default='', max_length=500), project: str = '', review_status: str = '',
                       tag: str = '', rights_status: str = '', collection: str = '', folder_id: str = '',
                       job_id: str | None = Query(default=None, max_length=200),
                       limit: int = Query(default=200, ge=1, le=500), offset: int = Query(default=0, ge=0)):
    matches = []
    jobs = list_jobs()
    scoped = [require_job(job_id)] if job_id is not None else jobs
    for job in _filtered(scoped, project=project, rights_status=rights_status, collection=collection, folder_id=folder_id):
        for shot in shot_views(job):
            if review_status and shot['review_status'] != review_status:
                continue
            if tag and tag.casefold() not in {t.casefold() for t in shot['tags']}:
                continue
            score, match = match_shot(q, shot)
            if score >= 0:
                matches.append((score, {**shot, **match}))
    matches.sort(key=lambda item: item[0], reverse=True)
    return {'shots': [s for _, s in matches[offset:offset + limit]], 'total': len(matches), 'search_type': 'keyword',
            'active_jobs': sum(j.status in ('queued', 'analyzing', 'processing') or cut_is_active(j) for j in jobs)}


@router.get('/projects')
async def projects(folder_id: str = ''):
    imports = []
    for job in _filtered(list_jobs(), folder_id=folder_id):
        view = asset_view(job)
        imports.append({'id': job.job_id, 'title': view['metadata']['title'],
                        'filename': job.filename, 'status': job.status, 'shot_count': view['shot_count'],
                        'folder_id': job.folder_id, 'has_local_media': bool(job.local_path or job.viewing_path)})
    return {'projects': imports, 'total': len(imports)}


@router.get('/folders')
async def list_folders():
    from app.services.library_folders import folders
    return folders()


def folder_action(action, *args):
    try:
        return action(*args)
    except KeyError as exc:
        raise HTTPException(404, str(exc.args[0])) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post('/folders', status_code=201)
async def create_folder(body: FolderName):
    from app.services.library_folders import save_folder
    return folder_action(save_folder, body.name)


@router.patch('/folders/{folder_id}')
async def rename_folder(folder_id: str, body: FolderName):
    from app.services.library_folders import save_folder
    return folder_action(save_folder, body.name, folder_id)


@router.delete('/folders/{folder_id}')
async def delete_folder(folder_id: str):
    from app.services.library_folders import remove_folder
    return folder_action(remove_folder, folder_id)


@router.post('/move')
async def move_to_folder(body: FolderMove):
    from app.services.library_folders import move_shots
    return folder_action(move_shots, body.job_ids, body.folder_id)


@router.get('/{job_id}')
async def asset_detail(job_id: str):
    return asset_view(require_job(job_id), detail=True)


@router.patch('/{job_id}')
async def edit_asset(job_id: str, metadata: AssetMetadata):
    job = require_job(job_id)
    merged = AssetMetadata(**{**job.metadata, **metadata.model_dump(exclude_unset=True)}).model_dump()
    return asset_view(update_job(job_id, metadata=merged), detail=True)


@router.patch('/{job_id}/shots/{shot_number}')
async def edit_shot(job_id: str, shot_number: int, annotation: ShotAnnotation,
                    expected_start_time: str | None = Query(default=None, max_length=100),
                    expected_end_time: str | None = Query(default=None, max_length=100)):
    job = require_job(job_id)
    shot = next((s for s in shot_views(job) if s['shot_number'] == shot_number), None)
    guarded = expected_start_time is not None or expected_end_time is not None
    if guarded and (shot is None
                    or (expected_start_time is not None and expected_start_time != shot.get('start_time'))
                    or (expected_end_time is not None and expected_end_time != shot.get('end_time'))):
        raise HTTPException(409, 'This shot changed during analysis. Reload the shot before saving your edits.')
    if shot is None:
        raise HTTPException(404, 'Shot not found')
    key = str(shot_number)
    merged = {**job.shot_annotations.get(key, {}), **annotation.model_dump(exclude_unset=True)}
    update_job(job_id, shot_annotations={**job.shot_annotations, key: ShotAnnotation(**merged).model_dump()})
    return {'shot_number': shot_number, **merged}


@router.get('/{job_id}/media')
async def media(job_id: str):
    job = require_job(job_id)
    if not job.local_path or not Path(job.local_path).is_file():
        raise HTTPException(404, 'Local media is unavailable for this asset')
    # Starlette FileResponse handles HTTP Range for seeking without loading the file into memory.
    from app.services.camera_media import browser_media
    try:
        path, mime = await browser_media(job)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return FileResponse(path, media_type=mime)


@router.get('/{job_id}/poster')
async def poster(job_id: str):
    job = require_job(job_id)
    path = Path(settings.upload_dir) / job_id / 'poster.jpg'
    # Repair legacy anamorphic JPEGs without replacing the recording or proxy.
    if job.technical.get('viewing_proxy', {}).get('decoder') == 'arri_raw':
        from app.services.camera_media import viewing_media
        from app.services.media_probe import create_poster
        corrected = path.with_name('poster-display-' + job.viewing_fingerprint[:16] + '.jpg')
        async with _poster_locks.setdefault(job_id, asyncio.Lock()):
            if not corrected.is_file() or not corrected.stat().st_size:
                try:
                    await create_poster(viewing_media(job), str(corrected), job.technical['duration_seconds'])
                except (ValueError, OSError, HTTPException):
                    pass  # An offline original must not discard the retained poster.
            if corrected.is_file() and corrected.stat().st_size:
                path = corrected
    if not path.is_file():
        raise HTTPException(404, 'Poster not available')
    return FileResponse(path, media_type='image/jpeg')


@router.post('/{job_id}/analyze', status_code=202)
async def analyze_asset(job_id: str, body: AnalyzeRequest, api_key: str | None = Depends(get_api_key)):
    job = require_job(job_id)
    from app.services.proxy_policy import proxy_decision
    try:
        proxy_decision(job, body.proxy_mode)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if job.media_origin == 'external':
        from app.services.editor_bridge import ensure_source_unchanged
        ensure_source_unchanged(job)
    if not (api_key or settings.google_api_key):
        raise HTTPException(400, 'Configure a Gemini key in Settings or GOOGLE_API_KEY in .env to analyze footage')
    if not claim_analysis(job_id):
        raise HTTPException(409, 'This asset is already queued or analyzing')
    start_job(job_id, body, api_key)
    return {'job_id': job_id, 'status': 'analyzing'}


@router.post('/{job_id}/cancel')
async def cancel_analysis(job_id: str):
    require_job(job_id)
    if not cancel_job(job_id):
        raise HTTPException(409, 'There is no active analysis to cancel')
    return {'status': 'cancelling'}


@router.get('/{job_id}/export')
async def export_asset(job_id: str, format: str = Query(default='json', pattern='^(json|csv|xmp|srt|edl|fcpxml|premiere_xml)$'),
                       shot_numbers: str | None = Query(default=None, max_length=20000),
                       analysis_started_at: str | None = Query(default=None, max_length=100)):
    from app.services.library_export import export_library, parse_shot_selection
    job = require_job(job_id)
    if format in ('edl', 'fcpxml', 'premiere_xml'):
        from app.services.source_timing import refresh_export_source
        job = await refresh_export_source(job)
    if analysis_started_at is not None and analysis_started_at != str(job.analysis_config.get('started_at') or ''):
        raise HTTPException(409, 'The analysis changed. Refresh the shots before exporting this selection.')
    try:
        selected = parse_shot_selection(shot_numbers)
        content, media_type, extension = export_library(job, format, selected)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    suffix = 'metadata' if selected is None else 'selected_shots'
    name = quote(f'{Path(job.filename).stem}_{suffix}.{extension}')
    return Response(content=content, media_type=media_type,
                    headers={'Content-Disposition': f"attachment; filename*=UTF-8''{name}"})


@router.get('/{job_id}/export-options')
async def available_exports(job_id: str, shot_numbers: str | None = Query(default=None, max_length=20000),
                            analysis_started_at: str | None = Query(default=None, max_length=100)):
    from app.services.library_export import export_options, parse_shot_selection
    job = require_job(job_id)
    from app.services.source_timing import refresh_export_source
    job = await refresh_export_source(job)
    if analysis_started_at is not None and analysis_started_at != str(job.analysis_config.get('started_at') or ''):
        raise HTTPException(409, 'The analysis changed. Refresh the shots before exporting this selection.')
    try:
        return export_options(job, parse_shot_selection(shot_numbers))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
