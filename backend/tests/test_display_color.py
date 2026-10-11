import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.config import settings
from app.services import display_color
from app.services.editor_bridge import source_fingerprint
from app.services.job_store import Job, create_job, get_job, update_job


def camera(tmp_path):
    source = tmp_path / 'original.mov'
    source.write_bytes(b'camera original must never change')
    fingerprint = source_fingerprint(source)[0]
    return create_job(Job(job_id=str(uuid4()), file_id='old-upload', file_uri='old-uri',
        filename=source.name, mime_type='video/quicktime', local_path=str(source),
        size_bytes=source.stat().st_size, media_origin='external', source_fingerprint=fingerprint,
        technical={'duration_seconds': 2, 'codec': 'h264', 'frame_rate': 24},
        flash_result={'scenes': []}, metadata={'title': 'My shot'}, folder_id='my-folder'))


def test_display_transform_is_reversible_and_does_not_stack_or_change_original(client, tmp_path):
    job = camera(tmp_path)
    seen = []
    async def render(original, folder, facts, *args, **kwargs):
        seen.append(original)
        target = folder / kwargs['target_name']; target.write_bytes(b'new display pixels')
        return target, {'generated': True, 'codec': 'h264'}
    async def poster(source, target, duration):
        Path(target).write_bytes(b'new poster')
    with patch.object(display_color, 'dji_lut', AsyncMock(return_value=b'verified LUT')), \
         patch.object(display_color, 'probe_media', AsyncMock(return_value=job.technical)), \
         patch.object(display_color, 'prepare_ffmpeg_viewing', AsyncMock(side_effect=render)), \
         patch.object(display_color, 'create_poster', AsyncMock(side_effect=poster)):
        first = asyncio.run(display_color.set_display_color(job.job_id, 'dji_zenmuse_dlog'))
        second = asyncio.run(display_color.set_display_color(job.job_id, 'dji_zenmuse_dlog'))
        restored = asyncio.run(display_color.set_display_color(job.job_id, 'source'))
    assert seen == [Path(job.local_path)] * 2
    assert first.viewing_path != second.viewing_path
    assert restored.viewing_path == '' and restored.viewing_fingerprint == ''
    assert restored.file_id == '' and restored.file_uri == ''
    assert restored.metadata == job.metadata and restored.folder_id == job.folder_id
    assert restored.flash_result == job.flash_result
    assert any('Analyze again' in warning for warning in restored.warnings)
    assert Path(job.local_path).read_bytes() == b'camera original must never change'
    assert not (Path(settings.upload_dir) / job.job_id / 'display-lut.cube').exists()


def test_failed_color_render_restores_status_and_keeps_previous_media(client, tmp_path):
    job = camera(tmp_path)
    with patch.object(display_color, 'dji_lut', AsyncMock(side_effect=ValueError('download failed'))):
        with pytest.raises(HTTPException, match='download failed'):
            asyncio.run(display_color.set_display_color(job.job_id, 'dji_zenmuse_dlog'))
    retained = get_job(job.job_id)
    assert retained.status == job.status and retained.viewing_path == job.viewing_path
    assert retained.technical == job.technical and retained.file_id == job.file_id


def test_x9_lut_grades_prores_original_not_lossy_viewing_copy(client, tmp_path):
    job = camera(tmp_path)
    preview = tmp_path / 'ungraded.mp4'
    preview.write_bytes(b'ungraded 8-bit viewing copy')
    update_job(job.job_id, viewing_path=str(preview), viewing_fingerprint=source_fingerprint(preview)[0],
               technical={**job.technical, 'codec': 'prores'})
    async def render(original, folder, facts, *args, **kwargs):
        assert original == Path(job.local_path)
        assert kwargs['lut'] is True
        assert (folder / 'display-lut.cube').read_bytes() == b'X9 LUT'
        target = folder / kwargs['target_name']; target.write_bytes(b'graded preview')
        return target, {'generated': True, 'codec': 'h264'}
    async def poster(source, target, duration):
        Path(target).write_bytes(b'graded poster')
    with patch.object(display_color, 'dji_x9_lut', AsyncMock(return_value=b'X9 LUT')) as x9, \
         patch.object(display_color, 'dji_lut', AsyncMock()) as legacy, \
         patch.object(display_color, 'probe_media', AsyncMock(return_value=job.technical)), \
         patch.object(display_color, 'prepare_ffmpeg_viewing', AsyncMock(side_effect=render)), \
         patch.object(display_color, 'create_poster', AsyncMock(side_effect=poster)):
        result = asyncio.run(display_color.set_display_color(job.job_id, 'dji_x9_dlog'))
        restored = asyncio.run(display_color.set_display_color(job.job_id, 'source'))
    x9.assert_awaited_once(); legacy.assert_not_awaited()
    assert result.technical['viewing_proxy']['lut_sha256'] == display_color.DJI_X9_SHA
    assert result.technical['viewing_proxy']['display_mode'] == 'dji_x9_dlog'
    assert restored.viewing_path == str(preview)
    assert preview.read_bytes() == b'ungraded 8-bit viewing copy'
    assert Path(job.local_path).read_bytes() == b'camera original must never change'


@pytest.mark.parametrize('status', ['queued', 'analyzing', 'processing', 'deleting'])
def test_color_cannot_race_analysis_or_deletion(client, tmp_path, status):
    job = camera(tmp_path); update_job(job.job_id, status=status)
    with pytest.raises(HTTPException) as error:
        asyncio.run(display_color.set_display_color(job.job_id, 'dji_zenmuse_dlog'))
    assert error.value.status_code == 409
    assert get_job(job.job_id).status == status


def test_red_transform_rejects_non_red_and_changed_original_is_not_processed(client, tmp_path):
    job = camera(tmp_path)
    with pytest.raises(HTTPException) as error:
        asyncio.run(display_color.set_display_color(job.job_id, 'red_rec709'))
    assert error.value.status_code == 422
    Path(job.local_path).write_bytes(b'changed original')
    with patch.object(display_color, 'dji_lut', AsyncMock()) as download:
        with pytest.raises(HTTPException):
            asyncio.run(display_color.set_display_color(job.job_id, 'dji_zenmuse_dlog'))
    download.assert_not_awaited()
    assert get_job(job.job_id).status == 'ready'
