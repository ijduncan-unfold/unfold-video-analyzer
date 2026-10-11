"""Acquire ARRI's official CMD package locally instead of redistributing it."""
import asyncio
import hashlib
import os
from pathlib import Path
import shutil
import sys
import uuid
import zipfile

import httpx
from app.config import settings
from app.services.native_camera import family_runtime, _sha

ART_SHA256 = 'ef27114b5126ee4c0f621a9612775ec0fad95964f46d2f8fa80afad7733b46f8'
SDK_SHA256 = '73e5ca6236bab04df681b34e67abc9759f83a4e87b16e4e0495dd57a81a5f328'
ARCHIVE_SHA256 = '36cd2269877a73ecd2247a4bc50201b4c93f7d0ed80659540b58ba010be5178d'
DOWNLOAD = ('https://arri.canto.de/direct/other/q6q26483mp3hn5nl785g297k7d/'
            'ezdjiW13460Tjr-ZalaTj2m4jSo/original?content-type=application%2Fzip&'
            'name=ARRIReferenceTool_CMD_1.0.0_win_msvc192_x64_data.zip')
PINS = {'art-cmd.exe': ART_SHA256, 'ArriImageSdk.9.dll': SDK_SHA256,
    'arriimagesdk_plugins/ArriImageSdkTransformsCpu_module.9.0.1.dll':
        '9d893ea2486fd5ee42321afbd4c0143856feceaf9c2c6e2b8d93d87404fb4cb1',
    'arriimagesdk_plugins/ArriImageSdkTransformsCpuFma_module.9.0.1.dll':
        'ce618bd3827f4ff4225a6ed9261e8d3e63ce54fdcc7a2c02f07b573906bdfffa'}
_lock = asyncio.Lock()


def verified(root):
    return all((root / name).is_file() and _sha(root / name) == digest for name, digest in PINS.items())


def extract(archive, root):
    with zipfile.ZipFile(archive) as source:
        for entry in source.infolist():
            parts = Path(entry.filename.replace('\\', '/')).parts
            # The downloaded package puts bin/ and doc/ inside its distribution directory.
            if 'bin' in parts:
                relative = Path(*parts[parts.index('bin') + 1:])
            elif parts and parts[-1] == 'EULA.txt':
                relative = Path('EULA.txt')
            else:
                continue
            target = (root / relative).resolve()
            if not target.is_relative_to(root.resolve()) or entry.file_size > 256 * 1024 * 1024:
                raise ValueError('Invalid ARRI decoder package')
            if entry.is_dir():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open(entry) as original, target.open('wb') as out:
                shutil.copyfileobj(original, out)
    if not verified(root):
        raise ValueError('ARRI decoder version verification failed')


async def arri_runtime(on_progress=None):
    if sys.platform != 'win32':
        raise ValueError('ARRI RAW decoding currently requires Windows x64')
    try:
        executable, _ = await asyncio.to_thread(family_runtime, 'arri', PINS)
        return executable
    except ValueError:
        pass
    # ARRI's module loader has a legacy Windows path-length limit. A full hash
    # directory pushed CPU module paths near 260 characters in custom data roots.
    # Binary/archive verification still uses the complete SHA-256 values.
    cache = Path(settings.database_path).resolve().parent / 'camera-decoders' / 'arri-art-1.0.0'
    async with _lock:
        if await asyncio.to_thread(verified, cache):
            return cache / 'art-cmd.exe'
        cache.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache.parent / (uuid.uuid4().hex + '.zip')
        staging = cache.parent / uuid.uuid4().hex
        try:
            digest, size = hashlib.sha256(), 0
            async with httpx.AsyncClient(follow_redirects=True, timeout=120) as client:
                async with client.stream('GET', DOWNLOAD) as response:
                    response.raise_for_status()
                    total = int(response.headers.get('content-length') or 0)
                    with temporary.open('wb') as out:
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > 64 * 1024 * 1024:
                                raise ValueError('ARRI download exceeds expected package size')
                            digest.update(chunk)
                            out.write(chunk)
                            if on_progress:
                                on_progress({'stage': 'preparing_proxy',
                                    'message': 'Downloading official ARRI decoder (first import)',
                                    'percent': min(99, round(size / total * 100, 1)) if total else 0})
            if digest.hexdigest() != ARCHIVE_SHA256:
                raise ValueError('ARRI official download changed; update the verified decoder package')
            extraction = asyncio.create_task(asyncio.to_thread(extract, temporary, staging))
            try:
                await asyncio.shield(extraction)
            except asyncio.CancelledError:
                # Let our bounded extraction finish before deleting its directory.
                await extraction
                raise
            cache.mkdir(parents=True, exist_ok=True)
            for file in staging.rglob('*'):
                if file.is_file():
                    destination = cache / file.relative_to(staging)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(file, destination)
            return cache / 'art-cmd.exe'
        except httpx.HTTPError:
            raise ValueError('ARRI decoder download failed. Connect to the internet and retry import.')
        finally:
            temporary.unlink(missing_ok=True)
            if staging.is_dir():
                shutil.rmtree(staging)
