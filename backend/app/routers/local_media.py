"""Same-origin, loopback-only registration; no originals are uploaded or copied."""
from urllib.parse import urlsplit
import os
import time
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.camera_media import register_camera_source
from app.services.library_service import asset_view
from app.config import settings


def require_local_import(request: Request):
    if not request.client or request.client.host not in {'127.0.0.1', '::1'}:
        raise HTTPException(403, 'Linked media import is available only on this computer.')
    if request.url.hostname not in {'127.0.0.1', 'localhost', '::1'}:
        raise HTTPException(403, 'Open the local app to link media.')
    origin = request.headers.get('origin')
    expected = urlsplit(str(request.base_url))
    if origin:
        supplied = urlsplit(origin)
        trusted_dev = supplied.hostname in {'127.0.0.1', 'localhost', '::1'} and origin in settings.allowed_origins
        if (supplied.scheme, supplied.netloc) != (expected.scheme, expected.netloc) and not trusted_dev:
            raise HTTPException(403, 'Linked media import requires the local app origin.')
    if request.headers.get('sec-fetch-site') not in {None, 'same-origin', 'none'} and not (
            origin in settings.allowed_origins and urlsplit(origin).hostname in {'127.0.0.1', 'localhost', '::1'}):
        raise HTTPException(403, 'Linked media import requires the local app origin.')
    if request.method not in {'GET', 'HEAD'} and request.headers.get('content-type', '').split(';')[0].strip() != 'application/json':
        raise HTTPException(415, 'Use the local app import form.')


router = APIRouter(prefix='/api/local-media', dependencies=[Depends(require_local_import)])
_import_progress = {}


class LocalMediaRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    media_path: str = Field(min_length=1, max_length=4096)
    viewing_path: str = Field(default='', max_length=4096)
    proxy_alignment_confirmed: bool = False
    import_id: UUID | None = None

    @field_validator('media_path', 'viewing_path')
    @classmethod
    def path_text(cls, value):
        value = value.strip().strip('"')
        if '\x00' in value:
            raise ValueError('Invalid file path')
        return value


@router.post('/import', status_code=201)
async def import_original(body: LocalMediaRequest):
    ident = str(body.import_id) if body.import_id else None
    now = time.monotonic()
    for key, value in list(_import_progress.items()):
        if value.get('stage') in {'complete', 'error'} and now - value['updated_at'] > 3600:
            _import_progress.pop(key, None)
    if ident and ident in _import_progress:
        raise HTTPException(409, 'This import is already running or completed. Use a new import identifier.')
    if len(_import_progress) >= 100:
        oldest = next((key for key, value in _import_progress.items() if value.get('stage') in {'complete', 'error'}), None)
        if oldest:
            _import_progress.pop(oldest, None)
    if len(_import_progress) >= 100:
        raise HTTPException(429, 'Too many imports; wait before importing more media.')
    def progress(state):
        if ident:
            _import_progress[ident] = {**state, 'updated_at': time.monotonic()}
    progress({'stage': 'inspecting', 'message': 'Reading original media'})
    try:
        kwargs = {'on_progress': progress} if ident else {}
        job = await register_camera_source(body.media_path, body.viewing_path, body.proxy_alignment_confirmed, **kwargs)
        progress({'stage': 'complete', 'message': 'Import complete', 'percent': 100})
        return asset_view(job, detail=True)
    except BaseException:
        progress({'stage': 'error', 'message': 'Import could not complete'})
        raise


@router.get('/import-progress/{import_id}')
def import_progress(import_id: UUID):
    state = _import_progress.get(str(import_id))
    if not state:
        return {'stage': 'pending', 'message': 'Reading original media'}
    return {key: value for key, value in state.items() if key != 'updated_at'}


class OpenDesktopRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    pick: bool = False


@router.post('/open-desktop')
def open_desktop(body: OpenDesktopRequest, request: Request):
    from app.desktop_window import launch_window
    from app.services.editor_bridge import get_pairing_token
    try:
        launch_window(str(request.base_url).rstrip('/'), get_pairing_token() if os.environ.get('VIDEO_ANALYZER_DESKTOP') == '1' else '', body.pick)
    except (ValueError, OSError) as exc:
        raise HTTPException(503, str(exc))
    return {'opened': True}
