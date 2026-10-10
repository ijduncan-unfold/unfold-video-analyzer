"""Durable local workspace storage. No provider credentials are persisted here."""
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from app.config import settings

_lock = RLock()


@dataclass
class Job:
    job_id: str
    file_id: str
    file_uri: str
    mime_type: str
    filename: str
    size_bytes: int
    local_path: str
    youtube_url: str | None = None
    status: str = "ready"
    current_pass: int | None = None
    current_scene: int | None = None
    total_scenes: int | None = None
    flash_result: dict | None = None
    deep_results: list[dict] = field(default_factory=list)
    summary: dict | None = None
    cost_estimate: dict | None = None
    custom_prompt: str | None = None
    custom_result: dict | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict = field(default_factory=dict)
    technical: dict = field(default_factory=dict)
    shot_annotations: dict = field(default_factory=dict)
    shot_matches: list[dict] = field(default_factory=list)
    transcript: list[dict] = field(default_factory=list)
    analysis_config: dict = field(default_factory=dict)
    analysis_history: list[dict] = field(default_factory=list)
    analysis_progress: dict = field(default_factory=dict)
    progress: str = "Ready to analyze"
    warnings: list[str] = field(default_factory=list)
    file_uploaded_at: str | None = None
    # External editor sources are referenced read-only, never owned by the library.
    media_origin: str = "upload"
    source_fingerprint: str = ""


@contextmanager
def connection():
    path = Path(settings.database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        db = sqlite3.connect(str(path), timeout=30)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS jobs (job_id TEXT PRIMARY KEY, data TEXT NOT NULL)")
            yield db
            db.commit()
        finally:
            db.close()


def _decode(value: str) -> Job:
    data = json.loads(value)
    data["created_at"] = datetime.fromisoformat(data["created_at"])
    return Job(**data)


def _save(db, job: Job):
    data = asdict(job)
    data["created_at"] = job.created_at.isoformat()
    db.execute("INSERT INTO jobs VALUES (?, ?) ON CONFLICT(job_id) DO UPDATE SET data=excluded.data",
               (job.job_id, json.dumps(data, ensure_ascii=False)))


def create_job(job: Job) -> Job:
    with connection() as db:
        _save(db, job)
    return job


def get_job(job_id: str) -> Job | None:
    with connection() as db:
        row = db.execute("SELECT data FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    return _decode(row[0]) if row else None


def list_jobs() -> list[Job]:
    with connection() as db:
        rows = db.execute("SELECT data FROM jobs ORDER BY rowid DESC").fetchall()
    return [_decode(row[0]) for row in rows]


def update_job(job_id: str, **kwargs) -> Job | None:
    with connection() as db:
        row = db.execute("SELECT data FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            return None
        job = _decode(row[0])
        for key, value in kwargs.items():
            if key not in Job.__dataclass_fields__ or key == "job_id":
                raise ValueError(f"Unknown or immutable job field: {key}")
            setattr(job, key, value)
        _save(db, job)
    return job


def claim_analysis(job_id: str) -> bool:
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT data FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            return False
        job = _decode(row[0])
        if job.status in ("analyzing", "queued", "processing", "deleting"):
            return False
        job.status, job.error, job.progress = "queued", None, "Waiting for analysis worker"
        job.analysis_progress = {
            "stage": "queued", "completed_sections": 0, "completed_shots": 0,
            "processed_seconds": 0, "total_seconds": job.technical.get("duration_seconds"),
            "total_sections": None, "failed_sections": 0,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        _save(db, job)
    return True


def claim_deletion(job_id: str) -> bool:
    """Reserve an idle asset before any remote or filesystem deletion work."""
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT data FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            return False
        job = _decode(row[0])
        if job.status in ("analyzing", "queued", "processing", "deleting"):
            return False
        job.status, job.error, job.progress = "deleting", None, "Deleting asset and derived media"
        _save(db, job)
    return True


def recover_interrupted_jobs():
    for job in list_jobs():
        if job.status in ("analyzing", "queued", "processing"):
            update_job(job.job_id, status="error", error="Analysis interrupted by server restart. Retry to continue.",
                       progress="Interrupted; previous results preserved",
                       analysis_progress={**job.analysis_progress, "stage": "interrupted",
                                          "updated_at": datetime.now(timezone.utc).isoformat()})
        elif job.status == "deleting":
            update_job(job.job_id, status="error", error="Deletion interrupted by server restart. Retry deletion to finish cleanup.",
                       progress="Deletion interrupted; retry deletion")


def delete_job(job_id: str) -> bool:
    with connection() as db:
        return db.execute("DELETE FROM jobs WHERE job_id=?", (job_id,)).rowcount > 0
