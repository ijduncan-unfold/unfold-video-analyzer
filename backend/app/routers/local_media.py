"""Same-origin, loopback-only registration; no originals are uploaded or copied."""
from urllib.parse import urlsplit

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
    if request.headers.get('content-type', '').split(';')[0].strip() != 'application/json':
        raise HTTPException(415, 'Use the local app import form.')


router = APIRouter(prefix='/api/local-media', dependencies=[Depends(require_local_import)])


class LocalMediaRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    media_path: str = Field(min_length=1, max_length=4096)
    viewing_path: str = Field(default='', max_length=4096)
    proxy_alignment_confirmed: bool = False

    @field_validator('media_path', 'viewing_path')
    @classmethod
    def path_text(cls, value):
        value = value.strip().strip('"')
        if '\x00' in value:
            raise ValueError('Invalid file path')
        return value


@router.post('/import', status_code=201)
async def import_original(body: LocalMediaRequest):
    job = await register_camera_source(body.media_path, body.viewing_path, body.proxy_alignment_confirmed)
    return asset_view(job, detail=True)
