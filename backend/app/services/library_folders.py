"""Library organization only. Folder operations never touch media files."""
import sqlite3
from uuid import uuid4

from app.services.job_store import connection, _decode, _save


def folders():
    with connection() as db:
        rows = db.execute('SELECT id, name FROM library_folders ORDER BY name_key').fetchall()
        counts = {}
        jobs = db.execute('SELECT data FROM jobs').fetchall()
        for row in jobs:
            folder = _decode(row[0]).folder_id
            counts[folder] = counts.get(folder, 0) + 1
    return {'folders': [{'id': fid, 'name': name, 'shot_count': counts.get(fid, 0)} for fid, name in rows],
            'total_shots': len(jobs), 'unfiled_shots': counts.get(None, 0)}


def save_folder(name, folder_id=None):
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        if folder_id and not db.execute('SELECT 1 FROM library_folders WHERE id=?', (folder_id,)).fetchone():
            raise KeyError('Folder not found')
        fid = folder_id or str(uuid4())
        try:
            if folder_id:
                db.execute('UPDATE library_folders SET name=?, name_key=? WHERE id=?', (name, name.casefold(), fid))
            else:
                db.execute('INSERT INTO library_folders VALUES (?, ?, ?)', (fid, name, name.casefold()))
        except sqlite3.IntegrityError as exc:
            raise ValueError('A folder with this name already exists') from exc
    return {'id': fid, 'name': name}


def move_shots(job_ids, folder_id):
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        if folder_id is not None and not db.execute('SELECT 1 FROM library_folders WHERE id=?', (folder_id,)).fetchone():
            raise KeyError('Folder not found')
        jobs = []
        for jid in dict.fromkeys(job_ids):
            row = db.execute('SELECT data FROM jobs WHERE job_id=?', (jid,)).fetchone()
            if not row:
                raise KeyError('A selected shot no longer exists. Refresh the library.')
            job = _decode(row[0])
            if job.status == 'deleting':
                raise ValueError('A selected shot is being removed. Refresh the library.')
            jobs.append(job)
        for job in jobs:
            job.folder_id = folder_id
            _save(db, job)
    return {'moved': len(jobs), 'folder_id': folder_id}


def remove_folder(folder_id):
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        if not db.execute('DELETE FROM library_folders WHERE id=?', (folder_id,)).rowcount:
            raise KeyError('Folder not found')
        for row in db.execute('SELECT data FROM jobs').fetchall():
            job = _decode(row[0])
            if job.folder_id == folder_id:
                job.folder_id = None
                _save(db, job)
    return {'status': 'deleted', 'note': 'Shots were moved to Unfiled. Media and analysis were kept.'}
