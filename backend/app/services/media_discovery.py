"""Enumerate original video paths without opening or copying their contents."""
import os
from pathlib import Path
import re
import stat

from app.utils.video_formats import get_mime_type

MAX_ENTRIES = 500_000
MAX_CLIPS = 10_000


def _recording_key(path):
    # A RED file part or ARRI image frame belongs to one recording, not one
    # library project per file. Import resolves and validates all companions.
    suffix = path.suffix.lower()
    pattern = r'(.+)_(\d{3})' if suffix == '.r3d' else r'(.+\.)(\d+)' if suffix in {'.ari', '.arx'} else None
    match = re.fullmatch(pattern, path.stem) if pattern else None
    if match:
        return (os.path.normcase(str(path.parent)), suffix, match[1].casefold()), int(match[2])
    return (os.path.normcase(str(path)),), 0


def discover_media(paths):
    recordings, visited, warnings = {}, set(), []
    entries = 0

    def warn(message):
        if len(warnings) < 20:
            warnings.append(message)

    def add(path):
        if '__MACOSX' in path.parts or not get_mime_type(path.name):
            return
        key, number = _recording_key(path)
        previous = recordings.get(key)
        if previous is None or number < previous[0]:
            recordings[key] = (number, str(path))
        if len(recordings) > MAX_CLIPS:
            raise ValueError(f'This selection contains more than {MAX_CLIPS:,} clips. Import smaller folders.')

    pending = []
    for value in paths:
        path = Path(value.strip().strip('"'))
        if not path.is_absolute():
            raise ValueError('Choose an original video or folder using its absolute path.')
        try:
            path = path.resolve(strict=True)
            if path.is_dir():
                pending.append(path)
            elif path.is_file():
                add(path)
        except OSError:
            warn(f'Could not access {path.name or path}: it may be offline or unavailable.')

    while pending:
        folder = pending.pop()
        key = os.path.normcase(str(folder))
        if key in visited:
            continue
        visited.add(key)
        try:
            with os.scandir(folder) as items:
                for item in items:
                    entries += 1
                    if entries > MAX_ENTRIES:
                        raise ValueError('This selection contains too many files. Import smaller folders.')
                    try:
                        # Do not follow symlinks/junctions into other folders or
                        # back around a network directory cycle.
                        if item.is_symlink():
                            continue
                        if item.is_dir(follow_symlinks=False):
                            if item.name == '__MACOSX':
                                continue
                            attributes = getattr(item.stat(follow_symlinks=False), 'st_file_attributes', 0)
                            if not attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                                pending.append(Path(item.path))
                        elif get_mime_type(item.name) and item.is_file(follow_symlinks=False):
                            add(Path(item.path))
                    except OSError:
                        warn(f'Could not access {item.name}: it may be offline or unavailable.')
        except OSError:
            warn(f'Could not read folder {folder.name}: check its access permissions or network connection.')

    return {'paths': sorted((item[1] for item in recordings.values()), key=str.casefold), 'warnings': warnings}
