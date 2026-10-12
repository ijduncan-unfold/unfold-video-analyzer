"""Retry cleanup of app-owned copies without keeping deleted projects in the library."""
import asyncio
import logging
from pathlib import Path
import shutil
from types import SimpleNamespace

from app.config import settings
from app.services.job_store import connection

logger = logging.getLogger(__name__)


def deletion_paths(job):
    root = Path(settings.upload_dir).resolve()
    asset_dir = (root / job.job_id).resolve()
    if asset_dir == root or asset_dir.parent != root or asset_dir.name != job.job_id:
        raise ValueError('Asset media directory is outside its expected workspace location')
    # Only the upload endpoint's exact naming convention establishes ownership.
    # External originals and other projects' copies must never be unlinked.
    candidate = Path(job.local_path).resolve() if job.local_path else None
    owned_copy = candidate if (getattr(job, 'media_origin', 'upload') != 'external'
                              and candidate and candidate.parent == root and candidate.stem == job.job_id) else None
    if getattr(job, 'media_origin', 'upload') == 'external' and candidate and candidate.is_relative_to(asset_dir):
        # Even an unusual original located inside this directory cannot be removed.
        asset_dir = None
    return owned_copy, asset_dir


def cleanup(job):
    owned_copy, asset_dir = deletion_paths(job)
    failures = []
    for path, directory in ((owned_copy, False), (asset_dir, True)):
        if path is None:
            continue
        try:
            if directory:
                if path.is_dir():
                    shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        except FileNotFoundError:
            pass  # Another in-flight cleanup already finished.
        except OSError as exc:
            failures.append(exc)
            logger.warning('media_cleanup_pending job=%s file=%s error=%s winerror=%s',
                           job.job_id, path.name, type(exc).__name__, getattr(exc, 'winerror', None))
    return not failures


def remove_record(job, pending):
    # Record cleanup and remove the project in one durable transaction.
    owned_copy, _ = deletion_paths(job)
    with connection() as db:
        db.execute('CREATE TABLE IF NOT EXISTS media_cleanup (job_id TEXT PRIMARY KEY, local_path TEXT NOT NULL)')
        # Retain choices made before display preferences were saved separately.
        mode = job.technical.get('display_color')
        if job.source_fingerprint and mode in {'source', 'dji_x9_dlog', 'dji_zenmuse_dlog', 'red_rec709'}:
            db.execute('CREATE TABLE IF NOT EXISTS source_display_color (fingerprint TEXT PRIMARY KEY, mode TEXT NOT NULL)')
            db.execute('INSERT OR REPLACE INTO source_display_color VALUES (?, ?)', (job.source_fingerprint, mode))
        if pending:
            db.execute('INSERT OR REPLACE INTO media_cleanup VALUES (?, ?)', (job.job_id, str(owned_copy) if owned_copy else ''))
        else:
            db.execute('DELETE FROM media_cleanup WHERE job_id=?', (job.job_id,))
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='editor_sources'").fetchone():
            db.execute('DELETE FROM editor_sources WHERE job_id=?', (job.job_id,))
        db.execute('DELETE FROM jobs WHERE job_id=?', (job.job_id,))


def retry_pending():
    with connection() as db:
        db.execute('CREATE TABLE IF NOT EXISTS media_cleanup (job_id TEXT PRIMARY KEY, local_path TEXT NOT NULL)')
        rows = db.execute('SELECT job_id,local_path FROM media_cleanup WHERE job_id NOT IN (SELECT job_id FROM jobs)').fetchall()
    for ident, local_path in rows:
        try:
            if not cleanup(SimpleNamespace(job_id=ident, local_path=local_path)):
                continue
        except ValueError:
            logger.warning('media_cleanup_unsafe_path job=%s', ident)
            continue
        with connection() as db:
            db.execute('DELETE FROM media_cleanup WHERE job_id=?', (ident,))


async def cleanup_worker():
    while True:
        try:
            await asyncio.to_thread(retry_pending)
        except Exception:
            logger.exception('Deferred media cleanup could not finish')
        await asyncio.sleep(30)
