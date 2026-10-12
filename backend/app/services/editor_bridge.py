"""Local editor pairing and read-only registration of original source media."""
import asyncio
import hashlib
import hmac
import os
import re
import secrets
import threading
import uuid
from pathlib import Path

from fastapi import Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import settings
from app.services.job_store import Job, _save, connection, get_job, list_jobs
from app.services.library_service import asset_view
from app.services.media_probe import create_poster, probe_media
from app.utils.video_formats import get_mime_type

_token_lock = threading.Lock()
_registration_lock = asyncio.Lock()


class EditorSource(BaseModel):
    model_config = ConfigDict(extra='forbid')
    project_id: str = Field(min_length=1, max_length=500)
    item_id: str = Field(min_length=1, max_length=500)
    media_path: str = Field(min_length=1, max_length=4096)
    name: str = Field(default='', max_length=250)
    source_fps: float | None = Field(default=None, gt=0, le=1000, allow_inf_nan=False)
    viewing_path: str = Field(default='', max_length=4096)
    proxy_alignment_confirmed: bool = False

    @field_validator('project_id', 'item_id', 'media_path')
    @classmethod
    def nonblank(cls, value):
        if not value.strip() or '\x00' in value:
            raise ValueError('Source identity and path must be nonempty and contain no null bytes')
        return value


# Keep the first integration's public Python contract compatible.
PremiereSource = EditorSource


def _editor_host(host):
    if host not in ('premiere', 'resolve'):
        raise ValueError('Unsupported editor host')
    return host


def get_pairing_token(rotate=False):
    """Only the launcher/CLI reads this; there is no unauthenticated token endpoint."""
    path = Path(settings.editor_token_path)
    with _token_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        if rotate:
            token = secrets.token_urlsafe(32)
            temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
            try:
                with temporary.open('x', encoding='utf-8') as output:
                    os.chmod(temporary, 0o600)
                    output.write(token)
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        else:
            try:
                with path.open('x', encoding='utf-8') as output:
                    os.chmod(path, 0o600)
                    output.write(secrets.token_urlsafe(32))
            except FileExistsError:
                pass
        token = path.read_text(encoding='utf-8').strip()
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
            raise ValueError('The local editor pairing token is invalid; rotate it with the pairing command')
        return token


async def require_editor(request: Request, authorization: str | None = Header(default=None)):
    if not request.client or request.client.host not in ('127.0.0.1', '::1'):
        raise HTTPException(403, 'Editor connections are available only on this computer')
    supplied = authorization.removeprefix('Bearer ') if authorization and authorization.startswith('Bearer ') else ''
    try:
        expected = get_pairing_token()
    except (OSError, ValueError):
        raise HTTPException(503, 'Local editor pairing is unavailable. Open editor pairing in the companion app.')
    if not supplied or not hmac.compare_digest(supplied.encode('utf-8'), expected.encode('utf-8')):
        raise HTTPException(401, 'Pair this editor with the local app using its pairing token',
                            headers={'WWW-Authenticate': 'Bearer'})


def _schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS editor_sources (
        host TEXT NOT NULL, project_id TEXT NOT NULL, item_id TEXT NOT NULL,
        job_id TEXT NOT NULL, media_path TEXT NOT NULL, name TEXT NOT NULL,
        fingerprint TEXT NOT NULL, PRIMARY KEY (host, project_id, item_id)
    )''')


def source_fingerprint(path):
    """Detect relinks/changes without scanning a feature film before every navigation."""
    resolved = Path(path).resolve(strict=True)
    stat = resolved.stat()
    if not resolved.is_file() or stat.st_size <= 0:
        raise ValueError('Select a nonempty local video file')
    identity = f'{os.path.normcase(str(resolved))}\n{stat.st_size}\n{stat.st_mtime_ns}\n{stat.st_dev}\n{stat.st_ino}'
    return hashlib.sha256(identity.encode('utf-8')).hexdigest(), resolved, stat.st_size


class OriginalMediaOffline(HTTPException):
    def __init__(self):
        super().__init__(409, 'The original media is offline. Relink it in your editor and load the selection again.')


class ViewingProxyOffline(HTTPException):
    def __init__(self):
        super().__init__(409, 'The linked viewing proxy is offline. Relink it before using this analysis.')


def ensure_original_unchanged(job):
    if job.media_origin != 'external':
        return
    try:
        fingerprint, _, _ = source_fingerprint(job.local_path)
    except (OSError, ValueError):
        raise OriginalMediaOffline()
    if not hmac.compare_digest(fingerprint, job.source_fingerprint):
        raise HTTPException(409, 'The source file changed. Load the editor selection again before using this analysis.')
    if job.camera_companions is not None:
        from app.services.camera_identity import recording_files
        try:
            _, files = recording_files(Path(job.local_path))
            current = [{'path': str(path.resolve()), 'fingerprint': source_fingerprint(path)[0]} for path in files]
        except (OSError, ValueError):
            raise HTTPException(409, 'A camera recording part or sidecar is missing or offline. Import the recording again.')
        if current != job.camera_companions:
            raise HTTPException(409, 'A camera recording part or sidecar changed. Import the recording again.')


def ensure_viewing_unchanged(job):
    if job.media_origin == 'external' and job.viewing_path:
        try:
            current, _, _ = source_fingerprint(job.viewing_path)
        except (OSError, ValueError):
            raise ViewingProxyOffline()
        if not hmac.compare_digest(current, job.viewing_fingerprint):
            raise HTTPException(409, 'The linked viewing proxy changed. Import the original and proxy again.')


def ensure_source_unchanged(job):
    ensure_original_unchanged(job)
    ensure_viewing_unchanged(job)


def editor_sources(job_id, host=None):
    if host is not None:
        _editor_host(host)
    with connection() as db:
        _schema(db)
        statement = 'SELECT host,project_id,item_id,media_path,name FROM editor_sources WHERE job_id=?'
        parameters = (job_id,)
        if host is not None:
            statement += ' AND host=?'
            parameters += (host,)
        rows = db.execute(statement, parameters).fetchall()
    return [dict(zip(('host', 'project_id', 'item_id', 'media_path', 'name'), row)) for row in rows]


def editor_view(job, host=None):
    sources = editor_sources(job.job_id, host=host)
    if job.media_origin == 'external':
        sources = [{**source, 'source_fps': job.technical.get('frame_rate')} for source in sources]
    return {**asset_view(job, detail=True), 'editor_sources': sources}


def saved_editor_view(job, host='premiere'):
    """Saved logging remains readable after relinking; native edits stay guarded."""
    result = editor_view(job, host=host)
    result['source_available'] = True
    result['source_media_path'] = job.local_path
    try:
        ensure_source_unchanged(job)
    except HTTPException as exc:
        result['source_available'] = False
        result['source_warning'] = exc.detail
    return result


def project_assets(project_id, host='premiere', include_saved=False):
    host = _editor_host(host)
    with connection() as db:
        _schema(db)
        rows = db.execute('''SELECT DISTINCT s.job_id FROM editor_sources s
            JOIN jobs j ON j.job_id=s.job_id WHERE s.host=? AND s.project_id=?
            ORDER BY j.rowid DESC''', (host, project_id)).fetchall()
    jobs = [job for row in rows if (job := get_job(row[0])) is not None]
    if include_saved:
        paths = {os.path.normcase(job.local_path) for job in jobs if job.media_origin == 'external'}
        ids = {job.job_id for job in jobs}
        jobs.extend(job for job in list_jobs() if job.job_id not in ids and job.status != 'deleting'
                    and job.media_origin == 'external' and os.path.normcase(job.local_path) in paths
                    and (job.flash_result or job.transcript))
        return [saved_editor_view(job, host=host) for job in jobs]
    return [editor_view(job, host=host) for job in jobs]


def _bind(job, body, path, fingerprint, new=False, host='premiere'):
    host = _editor_host(host)
    with connection() as db:
        _schema(db)
        if new:
            _save(db, job)
        db.execute('''INSERT INTO editor_sources VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(host,project_id,item_id) DO UPDATE SET job_id=excluded.job_id,
            media_path=excluded.media_path,name=excluded.name,fingerprint=excluded.fingerprint''',
            (host, body.project_id, body.item_id, job.job_id, str(path), body.name, fingerprint))


async def register_source(body, host='premiere'):
    host = _editor_host(host)
    requested = Path(body.media_path)
    if not requested.is_absolute():
        raise HTTPException(422, 'The editor must supply an absolute local media path')
    if not get_mime_type(requested.name):
        raise HTTPException(422, 'This source format is not supported yet. Select a local video file.')
    try:
        fingerprint, path, size = source_fingerprint(requested)
    except (OSError, ValueError):
        raise HTTPException(422, 'The selected source is offline, inaccessible, or empty. Relink it in your editor first.')
    # Desktop imports need not have any editor binding yet. Reuse their saved
    # analysis, including partial results, instead of creating an empty copy.
    if not body.viewing_path:
        async with _registration_lock:
            candidates = [job for job in list_jobs() if job.media_origin == 'external'
                          and job.source_fingerprint == fingerprint and job.status != 'deleting']
            candidates.sort(key=lambda job: bool(job.flash_result or job.transcript), reverse=True)
            if candidates:
                existing = candidates[0]
                ensure_source_unchanged(existing)
                _check_interpreted_rate(body.source_fps, existing.technical)
                _bind(existing, body, path, fingerprint, host=host)
                return editor_view(existing, host=host)
    from app.utils.video_formats import CAMERA_EXTENSIONS
    if body.viewing_path or path.suffix.lower() in CAMERA_EXTENSIONS:
        from app.services.camera_media import register_camera_source
        async with _registration_lock:
            job = await register_camera_source(str(path), body.viewing_path, body.proxy_alignment_confirmed, body.source_fps)
            _bind(job, body, path, fingerprint, host=host)
            return editor_view(job, host=host)
    # Serialize registration so selecting the same clip twice cannot create duplicate assets.
    async with _registration_lock:
        with connection() as db:
            _schema(db)
            row = db.execute('''SELECT s.job_id FROM editor_sources s JOIN jobs j ON j.job_id=s.job_id
                WHERE s.fingerprint=? ORDER BY j.rowid DESC LIMIT 1''', (fingerprint,)).fetchone()
        existing = get_job(row[0]) if row else None
        if existing and existing.status != 'deleting':
            _check_interpreted_rate(body.source_fps, existing.technical)
            _bind(existing, body, path, fingerprint, host=host)
            return editor_view(existing, host=host)
        try:
            technical = await probe_media(str(path))
        except FileNotFoundError:
            raise HTTPException(503, 'FFmpeg and ffprobe are required. Complete media-tool setup in the companion app.')
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        _check_interpreted_rate(body.source_fps, technical)
        try:
            current, _, _ = source_fingerprint(path)
        except (OSError, ValueError):
            raise HTTPException(409, 'The source changed during import. Reload your selection.')
        if current != fingerprint:
            raise HTTPException(409, 'The source changed during import. Reload your selection.')
        ident = str(uuid.uuid4())
        job = Job(job_id=ident, file_id='', file_uri='', filename=path.name,
                  mime_type=get_mime_type(path.name), size_bytes=size, local_path=str(path),
                  technical=technical, metadata={'title': body.name or path.stem[:250]},
                  media_origin='external', source_fingerprint=fingerprint)
        # Derived files go into the app's own cache. No copy, rename or write of the source.
        try:
            await create_poster(str(path), str(Path(settings.upload_dir) / ident / 'poster.jpg'),
                                technical['duration_seconds'])
        except (OSError, ValueError):
            job.warnings.append('The source was registered, but a preview thumbnail could not be created.')
        _bind(job, body, path, fingerprint, new=True, host=host)
        return editor_view(job, host=host)


def _check_interpreted_rate(source_fps, technical):
    if source_fps is None:
        return
    native = technical.get('frame_rate')
    if not isinstance(native, (int, float)) or native <= 0:
        raise HTTPException(422, 'The source frame rate could not be verified for editor navigation.')
    if abs(source_fps - native) > 0.001:
        raise HTTPException(422, 'Your editor has interpreted this clip at a different frame rate. '
                            'Use its original frame rate for this preview integration.')


class DesktopSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    google_api_key: str = Field(max_length=200)

    @field_validator('google_api_key')
    @classmethod
    def key_is_nonblank(cls, value):
        value = value.strip()
        if any(ord(c) < 32 for c in value):
            raise ValueError('Enter a Gemini API key')
        return value


def save_desktop_settings(body):
    if os.environ.get('VIDEO_ANALYZER_DESKTOP') != '1':
        raise HTTPException(409, 'This setting is managed by the development server environment.')
    path = Path(settings.database_path).parent / 'desktop-settings.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as output:
            os.chmod(temporary, 0o600)
            import json
            json.dump({'google_api_key': body.google_api_key}, output)
        os.replace(temporary, path)
    except OSError:
        raise HTTPException(503, 'The companion app could not save its settings. Check the user data folder.')
    finally:
        temporary.unlink(missing_ok=True)
    settings.google_api_key = body.google_api_key
