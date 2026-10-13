"""Saved whole-recording selections; native Premiere imports need no cut timebase."""
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from app.services.job_store import connection, get_job
from app.services.editor_bridge import ensure_original_unchanged
from app.services.editor_metadata import editor_metadata


def _schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS premiere_selections (id TEXT PRIMARY KEY, name TEXT NOT NULL, job_ids TEXT NOT NULL, created_at TEXT NOT NULL)')


def selections():
    with connection() as db:
        _schema(db)
        rows = db.execute('SELECT id, name, job_ids, created_at FROM premiere_selections ORDER BY created_at DESC').fetchall()
    return [{'id': ident, 'name': name, 'job_ids': json.loads(ids), 'count': len(json.loads(ids)), 'created_at': created}
            for ident, name, ids, created in rows]


def get_selection(ident):
    selection = next((item for item in selections() if item['id'] == ident), None)
    if selection is None:
        raise HTTPException(404, 'Saved selection not found.')
    return selection


def selection_job(job_id):
    job = get_job(job_id)
    if job is None or job.status == 'deleting':
        raise HTTPException(409, 'A selected recording was removed. Save a new selection from the library.')
    if not job.local_path or not Path(job.local_path).is_absolute() or not Path(job.local_path).is_file():
        raise HTTPException(409, f'{job.filename}: original media is offline. Reconnect it before importing into Premiere.')
    ensure_original_unchanged(job)
    return job


def save_selection(name, ids):
    if len(set(ids)) != len(ids):
        raise HTTPException(422, 'Select each recording only once.')
    for ident in ids:
        selection_job(ident)
    ident, created = str(uuid4()), datetime.now(timezone.utc).isoformat()
    with connection() as db:
        _schema(db)
        db.execute('INSERT INTO premiere_selections VALUES (?, ?, ?, ?)', (ident, name, json.dumps(ids), created))
    return get_selection(ident)


def import_plan(ident):
    selection = get_selection(ident)
    jobs = [selection_job(job_id) for job_id in selection['job_ids']]
    return {'schema': 'unfold.premiere-selection', 'id': ident, 'name': selection['name'],
            'items': [{'id': job.job_id, 'filename': job.filename, 'media_path': job.local_path} for job in jobs]}


def import_metadata(ident, job_id):
    if job_id not in get_selection(ident)['job_ids']:
        raise HTTPException(404, 'Recording is not in this selection.')
    job = selection_job(job_id)
    return {'media_path': job.local_path, 'mapping': editor_metadata(job, include_record=True)}
