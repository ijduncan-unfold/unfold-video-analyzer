import asyncio
import copy
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.services import camera_media, native_camera
from app.services.job_store import get_job, list_jobs, update_job
from app.services.media_cleanup import cleanup
from app.services.editor_bridge import ensure_source_unchanged
from app.utils.video_formats import get_mime_type

FACTS = {'duration_seconds': 10., 'frame_rate': 24., 'frame_rate_fraction': '24/1',
         'width': 4096, 'height': 2160, 'codec': 'h264', 'has_audio': True,
         'source_timecode': '01:00:00:00'}


def files(tmp_path, suffix='.mxf'):
    original = tmp_path / ('camera' + suffix)
    proxy = tmp_path / 'view.mp4'
    original.write_bytes(b'untouched camera original')
    proxy.write_bytes(b'user-owned viewing proxy')
    return original, proxy


@pytest.mark.parametrize('extension', ['.mxf', '.mov', '.mkv', '.m4v', '.mts', '.m2ts', '.braw', '.r3d', '.ari', '.arx', '.crm'])
def test_camera_formats_are_recognized_without_promising_a_decoder(extension):
    assert get_mime_type('CAMERA' + extension.upper())


def test_linked_import_is_idempotent_no_copy_and_original_and_proxy_survive_deletion(client, tmp_path):
    original, proxy = files(tmp_path)
    with patch.object(camera_media, 'probe_media', AsyncMock(return_value=FACTS)), \
         patch.object(camera_media, 'decode_check', AsyncMock(return_value=True)), \
         patch.object(camera_media, 'create_poster', AsyncMock()):
        job = asyncio.run(camera_media.register_camera_source(str(original), str(proxy), True))
        repeated = asyncio.run(camera_media.register_camera_source(str(original), str(proxy), True))
    assert job.job_id == repeated.job_id
    assert len(list_jobs()) == 1
    assert job.local_path == str(original.resolve()) and job.viewing_path == str(proxy.resolve())
    assert job.media_origin == 'external'
    assert cleanup(job)
    assert original.read_bytes() == b'untouched camera original'
    assert proxy.read_bytes() == b'user-owned viewing proxy'
    public = client.get('/api/library/' + job.job_id).json()
    assert public['filename'] == original.name and public['uses_viewing_proxy']
    assert 'viewing_path' not in public and 'local_path' not in public


@pytest.mark.parametrize('change', [{'duration_seconds': 9}, {'frame_rate': 25}, {'source_timecode': '02:00:00:00'}, {'width': 1920, 'height': 1080}])
def test_mismatched_viewing_copy_rejected_without_creating_project(client, tmp_path, change):
    original, proxy = files(tmp_path)
    different = {**FACTS, **change}
    with patch.object(camera_media, 'probe_media', AsyncMock(side_effect=[FACTS, different])), \
         patch.object(camera_media, 'decode_check', AsyncMock(return_value=True)):
        with pytest.raises(HTTPException) as error:
            asyncio.run(camera_media.register_camera_source(str(original), str(proxy), True))
    assert error.value.status_code == 422
    assert not list_jobs()


def test_unreadable_raw_requires_explicit_full_clip_proxy_confirmation(client, tmp_path):
    original, proxy = files(tmp_path, '.braw')
    with patch.object(camera_media, 'probe_media', AsyncMock(side_effect=ValueError('not readable'))), \
         patch.object(native_camera, 'prepare_braw_viewing', AsyncMock(side_effect=ValueError('Blackmagic RAW decoder unavailable'))):
        with pytest.raises(HTTPException, match='Blackmagic RAW'):
            asyncio.run(camera_media.register_camera_source(str(original)))
        with pytest.raises(HTTPException, match='Confirm'):
            asyncio.run(camera_media.register_camera_source(str(original), str(proxy)))
    with patch.object(camera_media, 'probe_media', AsyncMock(side_effect=[ValueError('vendor RAW'), FACTS])), \
         patch.object(camera_media, 'decode_check', AsyncMock(return_value=True)), \
         patch.object(camera_media, 'create_poster', AsyncMock()):
        job = asyncio.run(camera_media.register_camera_source(str(original), str(proxy), True))
    assert job.technical['width'] is None and job.technical['height'] is None
    assert job.technical['nominal_frame_rate_verified'] is False
    assert 'unverified' in job.technical['technical_basis']
    proxy.write_bytes(b'changed proxy')
    with pytest.raises(HTTPException, match='changed'):
        ensure_source_unchanged(job)


def test_sony_sidecar_recovers_raw_format_and_resolution_but_uses_probed_exact_rate(tmp_path):
    original, _ = files(tmp_path)
    original.with_name(original.stem + 'M01.xml').write_text('''<NonRealTimeMeta xmlns="urn:sony">
        <VideoFrame captureFps="23.98p" formatFps="23.98p" videoCodec="F55RAWSQ_4096_2160" />
        <VideoLayout pixel="4096" numOfVerticalLine="2160" />
        <Camera manufacturer="Sony" modelName="PMW-F55" />
        </NonRealTimeMeta>''')
    result = camera_media.sony_sidecar(original, {'codec': '', 'width': 0, 'height': 0,
                    'frame_rate_fraction': '0/0', 'nominal_frame_rate_fraction': '24000/1001'})
    assert result['codec'] == 'F55RAWSQ_4096_2160'
    assert result['width'] == 4096 and result['height'] == 2160
    assert result['frame_rate_fraction'] == '24000/1001'
    assert result['camera_metadata']['camera_model'] == 'PMW-F55'


def test_interpreted_camera_rate_rejected_before_creating_project(client, tmp_path):
    original, _ = files(tmp_path, '.mp4')
    with patch.object(camera_media, 'probe_media', AsyncMock(return_value=FACTS)), \
         patch.object(camera_media, 'decode_check', AsyncMock(return_value=True)), \
         patch.object(camera_media, 'create_poster', AsyncMock()) as poster:
        with pytest.raises(HTTPException, match='interpreted'):
            asyncio.run(camera_media.register_camera_source(str(original), source_fps=25))
    assert not list_jobs()
    poster.assert_not_called()


def test_link_route_rejects_remote_clients_and_cross_origin_before_reading_files(client, tmp_path):
    original, _ = files(tmp_path)
    with TestClient(app, base_url='http://127.0.0.1:8000', client=('127.0.0.1', 50000)) as local:
        with patch('app.routers.local_media.register_camera_source') as register:
            assert local.post('/api/local-media/import', json={'media_path': str(original)}, headers={'Origin': 'https://attacker.invalid'}).status_code == 403
            assert local.post('/api/local-media/import', json={'media_path': str(original)}, headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403
            assert local.post('/api/local-media/import', data={'media_path': str(original)}).status_code == 415
            register.assert_not_called()
    with TestClient(app, base_url='http://127.0.0.1:8000', client=('192.0.2.1', 50000)) as remote:
        assert remote.post('/api/local-media/import', json={'media_path': str(original)}).status_code == 403
    with TestClient(app, base_url='http://attacker.invalid', client=('127.0.0.1', 50000)) as rebound:
        assert rebound.post('/api/local-media/import', json={'media_path': str(original)}).status_code == 403


def test_same_origin_route_registers_original_without_file_payload(client, tmp_path):
    original, _ = files(tmp_path, '.mp4')
    with TestClient(app, base_url='http://127.0.0.1:8000', client=('127.0.0.1', 50000)) as local:
        with patch.object(camera_media, 'probe_media', AsyncMock(return_value=FACTS)), \
             patch.object(camera_media, 'decode_check', AsyncMock(return_value=True)), \
             patch.object(camera_media, 'create_poster', AsyncMock()):
            response = local.post('/api/local-media/import', json={'media_path': str(original)}, headers={'Origin': 'http://127.0.0.1:8000'})
    assert response.status_code == 201, response.text
    assert get_job(response.json()['job_id']).local_path == str(original.resolve())
