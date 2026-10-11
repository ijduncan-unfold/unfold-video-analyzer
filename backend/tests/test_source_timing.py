import copy
import asyncio

import pytest
from fastapi import HTTPException

from app.services.source_timing import camera_timing_fields, refresh_export_source, verified_rate


def facts():
    technical = {'frame_rate_fraction': '24000/1001', 'camera_frame_count': 880,
                 'duration_seconds': 36.703333, 'has_audio': True, 'audio_stream_count': 4}
    viewing = {'decoder': 'sony_f55_raw', 'generated': True, 'linked': False, 'frame_count': 880,
               'frame_rate_fraction': '24000/1001', 'duration_seconds': 36.703333}
    return technical, viewing


@pytest.mark.parametrize('decoder', ['sony_f55_raw', 'blackmagic_raw', 'red_raw'])
def test_complete_native_decode_certifies_exact_cadence(decoder):
    technical, viewing = facts()
    viewing['decoder'] = decoder
    result = camera_timing_fields(technical, viewing)
    assert verified_rate(result) == '24000/1001' and result['frame_rate_verified_frames'] == 880


@pytest.mark.parametrize('changes', [{'linked': True}, {'generated': False}, {'decoder': 'ffmpeg'},
    {'frame_count': 879}, {'frame_rate_fraction': '24/1'}, {'duration_seconds': 30}])
def test_user_proxy_or_incomplete_decode_cannot_certify_original(changes):
    technical, viewing = facts()
    viewing.update(changes)
    assert camera_timing_fields(technical, viewing) == {}


@pytest.mark.parametrize('failure', ['none', 'legacy_audio', 'changed_source', 'truncated_proxy', 'irregular_proxy', 'active'])
def test_existing_camera_import_refresh_preserves_analysis(asset, monkeypatch, failure):
    from app.services import editor_bridge, media_probe
    from app.services.job_store import update_job, get_job
    technical, viewing = facts()
    if failure == 'legacy_audio':
        technical.pop('audio_stream_count')
    job = update_job(asset.job_id, technical={**technical, 'viewing_proxy': viewing},
        media_origin='external', local_path='original.mxf', viewing_path='proxy.mp4',
        status='analyzing' if failure == 'active' else 'complete')
    calls = []

    def unchanged(snapshot):
        if failure == 'changed_source':
            raise HTTPException(409, 'Source changed')

    async def probe(path):
        calls.append(path)
        if path == 'original.mxf':
            return {'has_audio': True, 'audio_stream_count': 4, 'audio_channels': [1] * 4,
                    'audio_sample_rates': [48000] * 4}
        return {'nominal_frame_rate_verified': failure != 'irregular_proxy',
                'nominal_frame_rate_fraction': '24000/1001', 'frame_rate_verification': 'ffprobe_full_packet_pts',
                'frame_rate_verified_frames': 879 if failure == 'truncated_proxy' else 880,
                'frame_rate_fraction': '24000/1001', 'duration_seconds': viewing['duration_seconds']}

    monkeypatch.setattr(editor_bridge, 'ensure_source_unchanged', unchanged)
    monkeypatch.setattr(media_probe, 'probe_media', probe)
    before = copy.deepcopy(job.flash_result)
    result = asyncio.run(refresh_export_source(job))
    assert result.flash_result == before and get_job(job.job_id).flash_result == before
    assert bool(verified_rate(result.technical)) is (failure in ('none', 'legacy_audio'))
    if failure in ('changed_source', 'active'):
        assert calls == []
    elif failure in ('none', 'legacy_audio'):
        assert calls == ['proxy.mp4', 'original.mxf'] and result.technical['audio_channels'] == [1] * 4
