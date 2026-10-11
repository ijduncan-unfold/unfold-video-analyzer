"""Resolve browser drops only within already imported source folders.

Browsers supply names, not original paths. Compare bounded edge fingerprints,
size and modification time; never guess from a filename or scan a network share.
"""
import hashlib
import hmac
import os
from pathlib import Path

from app.services.job_store import list_jobs

EDGE_BYTES = 65536
MAX_FOLDERS = 256


def edge_digest(path, size):
    with path.open('rb') as stream:
        digest = hashlib.sha256(stream.read(EDGE_BYTES))
        if size > EDGE_BYTES:
            stream.seek(max(EDGE_BYTES, size - EDGE_BYTES))
            digest.update(stream.read(EDGE_BYTES))
        return digest.hexdigest()


def resolve_drop(name, size, modified_ms, digest):
    folders = {}
    for job in list_jobs():
        if job.media_origin == 'external' and job.local_path and job.status != 'deleting':
            source = Path(job.local_path)
            folder = source.parent
            folders[folder] = folders.get(folder, False) or folder.name.casefold() == source.stem.casefold()
    if len(folders) > MAX_FOLDERS:
        return None  # Cannot establish uniqueness within the bounded search.
    matches = {}
    for folder, recording_folder in folders.items():
        # Flat media folders and Sony's Clip/<recording>/<recording>.mxf layout.
        candidates = [(folder / name, folder)]
        if recording_folder:
            candidates.append((folder.parent / Path(name).stem / name, folder.parent))
        for candidate, root in candidates:
            try:
                resolved = candidate.resolve(strict=True)
                if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
                    continue
                before = resolved.stat()
                if before.st_size != size or abs(before.st_mtime_ns // 1_000_000 - modified_ms) > 1:
                    continue
                if hmac.compare_digest(edge_digest(resolved, size), digest):
                    after = resolved.stat()
                    if (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns):
                        matches[os.path.normcase(str(resolved))] = str(resolved)
            except OSError:
                continue
    return next(iter(matches.values())) if len(matches) == 1 else None
