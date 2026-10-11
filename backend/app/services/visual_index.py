"""Resumable frame index, isolated from paid analysis and invalidated by source/run changes."""
import asyncio
import hashlib
import json
import math
import sqlite3
from pathlib import Path

import cv2

from app.config import settings
from app.services.job_store import get_job
from app.services.library_service import shot_views
from app.services.thumbnail_service import _extract_frame
from app.services.visual_features import describe, compare
from app.services.camera_media import viewing_media

VERSION = 2
_tasks = {}
_errors = {}
_gate = asyncio.Semaphore(1)
_frame_gate = asyncio.Semaphore(2)


def source_info(job):
    if job.status in ('queued', 'analyzing', 'processing', 'deleting'):
        raise ValueError('Finish or cancel analysis before indexing visual matches.')
    media = viewing_media(job)
    if not media or not Path(media).is_file():
        raise ValueError('Visual matching needs the locally imported video.')
    shots = list(shot_views(job))
    if not shots:
        raise ValueError('Analyze this video to find shots first.')
    stat = Path(media).stat()
    key = hashlib.sha256(json.dumps([VERSION, str(Path(media).resolve()), stat.st_size, stat.st_mtime_ns,
        job.analysis_config.get('started_at'), [(s['shot_number'], s['start_seconds'], s['end_seconds']) for s in shots]]).encode()).hexdigest()[:24]
    return key, shots


def directory(job, key):
    # Hash opaque IDs: callers never control a filesystem path segment.
    root = Path(settings.upload_dir) / job.job_id
    if not root.resolve().is_relative_to(Path(settings.upload_dir).resolve()):
        raise ValueError('Invalid asset storage path.')
    return root / 'visual' / key


def connection(job, key):
    folder = directory(job, key)
    folder.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(folder / 'index.sqlite3', timeout=10)
    db.execute('CREATE TABLE IF NOT EXISTS frames (id TEXT PRIMARY KEY, shot INTEGER, seconds REAL, data TEXT)')
    return db


def samples(shots):
    # Five evenly distributed interior samples per shot. Dense trim review is interactive.
    for shot in shots:
        start, end = shot['start_seconds'], shot['end_seconds']
        if not math.isfinite(start + end) or end <= start:
            continue
        for seconds in sorted({round(start + (end - start) * f, 3) for f in (.1, .3, .5, .7, .9)}):
            yield shot, seconds, f"{shot['shot_number']}_{round(seconds * 1000)}"


def status(job):
    from app.services import composition_index, shape_index
    key, shots = source_info(job)
    total = sum(1 for _ in samples(shots))
    db = connection(job, key)
    try:
        done, failed = db.execute("SELECT COUNT(*), COALESCE(SUM(data IS NULL),0) FROM frames").fetchone()
    finally:
        db.close()
    running = job.job_id in _tasks and not _tasks[job.job_id].done()
    return {'job_id': job.job_id, 'revision': key, 'total': total, 'indexed': done - failed, 'failed': failed,
            'status': 'indexing' if running else 'complete' if done == total and not failed else 'partial' if done else 'not_started',
            'error': _errors.get(job.job_id), 'sampling': '5 interior frames per shot',
            'composition': composition_index.status(job, key), 'shape': shape_index.status(job, key)}


async def frame(job, key, seconds):
    folder = directory(job, key)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f'frame_{round(seconds * 1000)}.jpg'
    async with _frame_gate:
        if not path.is_file() and not await _extract_frame(viewing_media(job), seconds, str(path)):
            raise ValueError('Could not decode this frame. Try another point in the shot.')
    return path


def features(path):
    image = cv2.imread(str(path))
    if image is None:
        raise ValueError('Could not read this frame.')
    return describe(image)


async def build(job, key, shots):
    try:
        async with _gate:
            db = connection(job, key)
            try:
                done = {row[0] for row in db.execute('SELECT id FROM frames WHERE data IS NOT NULL')}
            finally:
                db.close()
            for shot, seconds, ident in samples(shots):
                if ident in done:
                    continue
                current = get_job(job.job_id)
                if current is None or source_info(current)[0] != key:
                    raise ValueError('The source or analysis changed. Restart the visual index.')
                try:
                    path = await frame(job, key, seconds)
                    data = await asyncio.to_thread(features, path)
                except ValueError:
                    data = None
                # Do not republish into an asset that has been removed or replaced during decoding.
                current = get_job(job.job_id)
                if current is None or source_info(current)[0] != key:
                    raise ValueError('The source or analysis changed. Restart the visual index.')
                db = connection(job, key)
                try:
                    db.execute('INSERT OR REPLACE INTO frames VALUES (?,?,?,?)',
                               (ident, shot['shot_number'], seconds, json.dumps(data) if data else None))
                    db.commit()
                finally:
                    db.close()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _errors[job.job_id] = str(exc) if isinstance(exc, ValueError) else 'Indexing stopped. Retry to resume saved frames.'


def start(job):
    key, shots = source_info(job)
    if job.job_id not in _tasks or _tasks[job.job_id].done():
        _errors.pop(job.job_id, None)
        _tasks[job.job_id] = asyncio.create_task(build(job, key, shots))
    return status(job)


async def stop(job_id):
    from app.services.composition_index import stop as stop_composition
    from app.services.shape_index import stop as stop_shape
    await stop_composition(job_id)
    await stop_shape(job_id)
    task = _tasks.get(job_id)
    if task and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    _tasks.pop(job_id, None)


async def shutdown():
    from app.services.composition_index import shutdown as shutdown_composition
    from app.services.shape_index import shutdown as shutdown_shape
    await shutdown_composition()
    await shutdown_shape()
    for job_id in list(_tasks):
        await stop(job_id)


def validate_frame(job, shot_number, seconds, revision):
    key, shots = source_info(job)
    if revision and key != revision:
        raise ValueError('The analysis changed. Reopen Match cuts before continuing.')
    shot = next((s for s in shots if s['shot_number'] == shot_number), None)
    if not shot or not shot['start_seconds'] <= seconds < shot['end_seconds']:
        raise ValueError('Choose a frame inside the selected shot.')
    return key, shot


def search(source, source_job, source_shot, targets, weights, region, limit, source_composition=None, source_form=None, align_shape=False):
    from app.services import composition_index, shape_index, shape_geometry
    best = {}
    states = []
    for job in targets:
        state = status(job)
        states.append(state)
        key, shots = source_info(job)
        profiles = composition_index.cached(job, key) if weights['composition'] else {}
        silhouettes = shape_index.cached(job, key) if weights['shape'] else {}
        by_number = {s['shot_number']: s for s in shots}
        db = connection(job, key)
        try:
            for shot_number, seconds, raw in db.execute('SELECT shot,seconds,data FROM frames WHERE data IS NOT NULL'):
                if job.job_id == source_job and shot_number == source_shot:
                    continue
                data = json.loads(raw)
                if not data['usable']:
                    continue
                # Contours are legacy descriptors; paid shape/framing never fall back to them.
                score = compare(source, data, {'shape': 0, 'composition': 0, 'color': 1})
                if weights['shape']:
                    if source_form is None:
                        continue
                    forms = silhouettes.get(round(seconds * 1000), {}).get('forms', [])
                    candidates = [(shape_geometry.compare(source_form, f, align_shape), f) for f in forms]
                    candidates = [(s, f) for s, f in candidates if s is not None]
                    if not candidates:
                        continue
                    geometry, form = max(candidates, key=lambda item: item[0]['score'])
                    score.update(source_box=None, target_box=None, source_outline=source_form['outline'],
                                 target_outline=form['outline'], shape_label=form['label'], shape_geometry=geometry,
                                 box_basis='Gemini silhouette', shape_summary=f"{source_form['label']} → {form['label']}")
                    score['scores']['shape'] = geometry['score']
                if weights['composition']:
                    profile = profiles.get(round(seconds * 1000))
                    framing = composition_index.compare(source_composition, profile)
                    if framing is None or framing['score'] < .65:
                        continue
                    score['scores']['composition'] = framing['score']
                    score['score'] = round(sum(score['scores'][k] * weights[k] for k in weights) / sum(weights.values()), 4)
                    score['reasons'] = framing['reasons']
                    score['composition_summary'] = framing['summary']
                    if not weights['shape']:
                        score['source_box'] = composition_index.xywh(source_composition)
                        score['target_box'] = composition_index.xywh(profile)
                        score['box_basis'] = 'Gemini dominant subject'
                score['score'] = round(sum(score['scores'][k] * weights[k] for k in weights) / sum(weights.values()), 4)
                shot = by_number.get(shot_number)
                if not shot:
                    continue
                ident = (job.job_id, shot_number)
                if ident in best and best[ident]['score'] >= score['score']:
                    continue
                best[ident] = {**score, 'job_id': job.job_id, 'shot_number': shot_number, 'seconds': seconds,
                    'aspect_ratio': data.get('aspect_ratio', (job.technical.get('width') or 16) / (job.technical.get('height') or 9)),
                    'revision': key, 'start_seconds': shot['start_seconds'], 'end_seconds': shot['end_seconds'],
                    'title': job.metadata.get('title') or job.filename, 'filename': job.filename, 'description': shot['visual_description'],
                    'frame_url': f'/api/visual/{job.job_id}/frame?shot_number={shot_number}&seconds={seconds}&revision={key}',
                    'media_url': f'/api/library/{job.job_id}/media'}
        finally:
            db.close()
    return {'matches': sorted(best.values(), key=lambda item: item['score'], reverse=True)[:limit], 'indexes': states,
            'score_basis': 'gemini_silhouettes_and_visual_measurements' if weights['shape'] else 'gemini_framing_and_local_visual_measurements' if weights['composition'] else 'local_visual_measurements',
            'source_composition': source_composition, 'source_shape': source_form, 'motion_supported': False}
