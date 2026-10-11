import asyncio
import ctypes
from fractions import Fraction
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch
import zipfile

import pytest
from fastapi import HTTPException

from app.config import settings
from app.services import camera_media, native_camera, arri_runtime
from app.services.arri_camera import metadata_sets
from app.services.braw_worker import rate_fraction
from app.services.camera_identity import recording_files
from app.services.editor_bridge import ensure_source_unchanged
from app.services.job_store import list_jobs
from app.services.media_cleanup import cleanup
from app.services.red_worker import aligned_buffer


def source(tmp_path, name):
    file = tmp_path / name
    file.write_bytes(b'original recording')
    return file


def test_recording_parts_canonicalize_without_glob_interpreting_filename(tmp_path):
    first = source(tmp_path, 'A[1]_001.R3D')
    second = source(tmp_path, 'A[1]_002.R3D')
    sidecar = source(tmp_path, 'A[1].RMD')
    assert recording_files(second) == (first, [second, sidecar])
    source(tmp_path, 'A[1]_004.R3D')
    with pytest.raises(ValueError, match='missing'):
        recording_files(second)


def test_arri_sequence_numeric_order_and_missing_frames(tmp_path):
    first = source(tmp_path, 'take.9.ari')
    second = source(tmp_path, 'take.10.ari')
    assert recording_files(second) == (first, [second])
    source(tmp_path, 'take.12.ari')
    with pytest.raises(ValueError, match='missing'):
        recording_files(first)


@pytest.mark.parametrize('extension,decoder', [('.braw', 'blackmagic_raw'), ('.r3d', 'red_raw')])
def test_native_import_retains_original_and_reuses_derivative(client, tmp_path, extension, decoder):
    original = source(tmp_path, 'take' + extension)
    original_bytes = original.read_bytes()
    facts = {'codec': extension[1:], 'width': 4096, 'height': 2160,
             'duration_seconds': 2, 'frame_rate': 24, 'frame_rate_fraction': '24/1',
             'nominal_frame_rate_verified': True, 'camera_frame_count': 48,
             'viewing_proxy': {'codec': 'h264', 'generated': True, 'decoder': decoder}}
    async def develop(path, folder, progress=None, family=None):
        assert path == original.resolve()
        folder.mkdir(parents=True)
        proxy = folder / 'viewing.mp4'
        proxy.write_bytes(b'derived pixels')
        return proxy, facts.copy()
    with patch.object(camera_media, 'probe_media', AsyncMock(side_effect=ValueError('vendor codec'))), \
         patch.object(native_camera, 'prepare_braw_viewing', AsyncMock(side_effect=develop)) as adapter, \
         patch.object(camera_media, 'create_poster', AsyncMock()):
        job = asyncio.run(camera_media.register_camera_source(str(original)))
        assert asyncio.run(camera_media.register_camera_source(str(original))).job_id == job.job_id
    adapter.assert_awaited_once()
    assert job.local_path == str(original.resolve()) and job.media_origin == 'external'
    assert job.technical['codec'] == extension[1:]
    assert cleanup(job)
    assert original.read_bytes() == original_bytes
    assert not Path(job.viewing_path).exists()


def test_changed_camera_sidecar_blocks_stale_cache_and_new_import_develops_again(client, tmp_path):
    original = source(tmp_path, 'take.braw')
    sidecar = source(tmp_path, 'take.sidecar')
    async def develop(path, folder, *args, **kwargs):
        folder.mkdir(parents=True)
        proxy = folder / 'viewing.mp4'
        proxy.write_bytes(b'derived')
        return proxy, {'duration_seconds': 2, 'frame_rate': 24, 'frame_rate_fraction': '24/1',
                      'viewing_proxy': {'codec': 'h264', 'generated': True}}
    with patch.object(camera_media, 'probe_media', AsyncMock(side_effect=ValueError('vendor codec'))), \
         patch.object(native_camera, 'prepare_braw_viewing', AsyncMock(side_effect=develop)) as adapter, \
         patch.object(camera_media, 'create_poster', AsyncMock()):
        job = asyncio.run(camera_media.register_camera_source(str(original)))
        sidecar.write_bytes(b'updated camera development settings')
        with pytest.raises(HTTPException, match='sidecar changed'):
            ensure_source_unchanged(job)
        changed = asyncio.run(camera_media.register_camera_source(str(original)))
    assert changed.job_id != job.job_id and adapter.await_count == 2


@pytest.mark.parametrize('rate,expected', [(23.976024627685547, Fraction(24000, 1001)),
    (29.970029830932617, Fraction(30000, 1001)), (24, Fraction(24))])
def test_sdk_float_rates_preserve_edit_cadence(rate, expected):
    assert rate_fraction(rate) == expected


def test_red_buffers_hold_alignment_and_reject_unbounded_allocation():
    for alignment in (16, 512):
        owner, pointer = aligned_buffer(4096, alignment)
        assert pointer % alignment == 0
        ctypes.memset(pointer, 7, 4096)
        assert ctypes.string_at(pointer, 4096) == b'\x07' * 4096
        assert ctypes.addressof(owner) <= pointer < ctypes.addressof(owner) + alignment
    with pytest.raises(ValueError):
        aligned_buffer(65 * 1024 * 1024, 16)


def test_arri_metadata_reads_static_prefix_without_loading_per_frame_tail(tmp_path):
    file = tmp_path / 'metadata.json'
    head = {'clipBasedMetadataSets': [{'metadataSetName': 'Clip Info', 'metadataSetPayload': {'clipDuration': 48}}],
            'descriptiveMetadataSets': [{'metadataSetName': 'Project Rate', 'metadataSetPayload': {'timebase': '24/1'}}]}
    file.write_text(json.dumps(head)[:-1] + ',"perFrameMetadata":' + '[' * 1_000_000)
    assert metadata_sets(file)['Clip Info']['clipDuration'] == 48
    file.write_text('{"unrelated":"missing metadata"}')
    with pytest.raises(ValueError):
        metadata_sets(file)


def test_official_arri_package_rejects_escape_before_extracting(tmp_path):
    archive = tmp_path / 'malicious.zip'
    with zipfile.ZipFile(archive, 'w') as out:
        out.writestr('bin/../../outside.dll', b'untrusted')
    with pytest.raises(ValueError, match='Invalid'):
        arri_runtime.extract(archive, tmp_path / 'decoder')
    assert not (tmp_path / 'outside.dll').exists()


def test_empty_cached_manifest_cannot_bypass_runtime_pins(client, monkeypatch, tmp_path):
    monkeypatch.delenv('VIDEO_ANALYZER_DECODER_DIR', raising=False)
    cache = Path(settings.database_path).parent / 'camera-decoders' / 'not-real'
    cache.mkdir(parents=True)
    (cache / 'complete.json').write_text('{}')
    with pytest.raises(ValueError, match='unverified'):
        native_camera.family_runtime('red', {'missing.dll': 'not-real'})
