"""Discover the members of a camera recording without copying or opening media."""
from pathlib import Path
import re


def recording_files(path):
    path = Path(path)
    companions = []
    kind = path.suffix.lower()
    if kind == '.r3d':
        match = re.fullmatch(r'(.+)_(\d{3})', path.stem)
        if match:
            files = sorted(p for p in path.parent.iterdir() if
                p.suffix.lower() == '.r3d' and re.fullmatch(re.escape(match[1]) + r'_\d{3}', p.stem))
            if files:
                numbers = [int(p.stem[-3:]) for p in files]
                if numbers != list(range(1, len(files) + 1)):
                    raise ValueError('This RED recording is missing a numbered file part')
                path, companions = files[0], files[1:]
            sidecar = path.with_name(match[1] + '.RMD')
        else:
            sidecar = path.with_suffix('.RMD')
        if sidecar.is_file():
            companions.append(sidecar)
    elif kind in {'.ari', '.arx'}:
        match = re.fullmatch(r'(.+\.)(\d+)', path.stem)
        if match:
            files = sorted((p for p in path.parent.iterdir() if p.suffix.lower() == kind and
                           re.fullmatch(re.escape(match[1]) + r'\d+', p.stem)),
                           key=lambda p: int(p.stem[len(match[1]):]))
            if files:
                numbers = [int(p.stem[len(match[1]):]) for p in files]
                if numbers != list(range(numbers[0], numbers[0] + len(files))):
                    raise ValueError('This ARRI recording is missing a frame in its sequence')
                path, companions = files[0], files[1:]
    elif kind == '.braw':
        sidecar = path.with_suffix('.sidecar')
        if sidecar.is_file():
            companions.append(sidecar)
    elif kind == '.mxf':
        sidecar = path.with_name(path.stem + 'M01.xml')
        if sidecar.is_file():
            companions.append(sidecar)
    if len(companions) > 250_000:
        raise ValueError('This camera sequence exceeds the current import limit')
    return path, companions
