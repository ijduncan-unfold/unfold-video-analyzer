"""Speech-aware edits: mock Gemini, source protection and executable XML contracts."""
import asyncio
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.config import settings
from app.models.rough_cut import RoughCutRequest
from app.services import rough_cut
from app.services.job_store import claim_analysis, claim_deletion, claim_rough_cut, get_job, recover_interrupted_jobs, update_job


def prepare(asset, tmp_path, duration=240):
    source = tmp_path / 'original.mp4'
    source.write_bytes(b'original recording never modified')
    job = update_job(asset.job_id, local_path=str(source), technical={
        'duration_seconds': duration, 'has_audio': True, 'width': 1920, 'height': 1080,
        'frame_rate': 24, 'frame_rate_fraction': '24'})
    draft = rough_cut.new_draft(job, RoughCutRequest(mode='condense', target_seconds=60))
    assert claim_rough_cut(job.job_id, draft)
    return job, draft, source


def moment(mid, start, end, text='A complete thought'):
    window = int(start // 120) * 120
    return {'moment_id': mid, 'start_seconds': start, 'end_seconds': end,
        'window_start': window, 'window_end': window + 120, 'text': text,
        'visual': 'Speaker demonstrates a feature', 'kind': 'usable', 'reason': 'Clear delivery'}


def ready(job, draft):
    first, second = moment('1:1', 2, 5), moment('2:1', 122, 126)
    draft.update(status='ready', moments=[first, second], clips=[first, second], duration_seconds=7)
    update_job(job.job_id, rough_cut=draft)
    return draft


def provider():
    client = Mock()
    async def generate(**kwargs):
        schema = kwargs['config'].response_schema
        if 'moments' in schema.properties:
            payload = {'moments': [{'start_time': '00:02', 'end_time': '00:05', 'text': 'A clear explanation',
                'visual': 'Presenter holds up the device', 'kind': 'usable', 'reason': 'Complete thought'}]}
            start = float(kwargs['contents'][0].video_metadata.start_offset[:-1])
            if start:
                payload['moments'][0].update(start_time='02:02', end_time='02:06')
        else:
            payload = {'moment_ids': ['1:1', '2:1'], 'rationale': 'Keep the explanation and demonstration'}
        return SimpleNamespace(text=json.dumps(payload), usage_metadata=None, candidates=[])
    client.aio.models.generate_content = AsyncMock(side_effect=generate)
    return client


def test_claim_blocks_other_analysis_and_deletion_and_restart_recovers(asset, tmp_path):
    job, draft, source = prepare(asset, tmp_path)
    assert not claim_analysis(job.job_id)
    assert not claim_deletion(job.job_id)
    assert not claim_rough_cut(job.job_id, draft)
    recover_interrupted_jobs()
    restored = get_job(job.job_id)
    assert restored.rough_cut['status'] == 'error'
    assert restored.flash_result == job.flash_result
    assert source.exists()


def test_provider_schema_uses_supported_subset_but_local_limits_remain():
    from app.models.rough_cut import SpeechMoments
    wire = rough_cut.provider_schema(SpeechMoments).model_dump(exclude_none=True)
    assert 'minLength' not in json.dumps(wire) and 'maxLength' not in json.dumps(wire)
    assert 'min_length' not in json.dumps(wire) and 'max_length' not in json.dumps(wire)
    with pytest.raises(ValueError):
        SpeechMoments.model_validate({'moments': [{'start_time': '0', 'end_time': '1', 'text': 'x' * 4001}]})


def test_create_requires_key_and_audio_and_claims_once(client, asset, tmp_path):
    job, draft, source = prepare(asset, tmp_path)
    update_job(job.job_id, rough_cut=None)
    assert client.post(f'/api/library/{job.job_id}/rough-cut', json={}).status_code == 400
    settings.google_api_key = 'not-real'
    with patch('app.routers.rough_cut.rough_cut.start') as start:
        result = client.post(f'/api/library/{job.job_id}/rough-cut', json={'mode': 'cleanup'})
        assert result.status_code == 202
        assert client.post(f'/api/library/{job.job_id}/rough-cut', json={}).status_code == 409
        assert client.delete(f'/api/files/{job.job_id}').status_code == 409
        start.assert_called_once()
    assert client.get('/api/library').json()['active_jobs'] == 1
    assert source.read_bytes() == b'original recording never modified'
    update_job(job.job_id, rough_cut=None, technical={**job.technical, 'has_audio': False})
    assert client.post(f'/api/library/{job.job_id}/rough-cut', json={}).status_code == 400


def test_manual_reorder_trim_persists_and_stale_revision_is_rejected(client, asset, tmp_path):
    job, draft, source = prepare(asset, tmp_path)
    draft = ready(job, draft)
    edited = [{'moment_id': '2:1', 'start_seconds': 123, 'end_seconds': 126},
              {'moment_id': '1:1', 'start_seconds': 2, 'end_seconds': 4}]
    response = client.patch(f'/api/library/{job.job_id}/rough-cut', json={'revision': draft['revision'], 'clips': edited})
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved['review_status'] == 'edited' and saved['duration_seconds'] == 5
    assert [c['moment_id'] for c in saved['clips']] == ['2:1', '1:1']
    assert saved['clips'][0]['start_time'] == '02:03.000'
    assert client.patch(f'/api/library/{job.job_id}/rough-cut', json={'revision': draft['revision'], 'clips': edited}).status_code == 409
    assert client.get(f'/api/library/{job.job_id}').json()['rough_cut']['revision'] == saved['revision']
    assert source.read_bytes() == b'original recording never modified'


def test_export_preserves_reviewed_order_and_contiguous_record_offsets(client, asset, tmp_path):
    job, draft, source = prepare(asset, tmp_path)
    draft = ready(job, draft); draft['clips'].reverse()
    update_job(job.job_id, rough_cut=draft)
    response = client.get(f'/api/library/{job.job_id}/rough-cut/export', params={'revision': draft['revision'], 'format': 'fcpxml'})
    assert response.status_code == 200, response.text
    root = ET.fromstring(response.text)
    clips = root.findall('.//spine/asset-clip')
    assert [c.attrib['start'] for c in clips] == ['2928/24s', '48/24s']
    assert [c.attrib['offset'] for c in clips] == ['0/24s', '96/24s']
    assert root.find('.//sequence').attrib['duration'] == '168/24s'
    assert root.find('.//asset').attrib['hasAudio'] == '1'
    assert root.find('.//media-rep').attrib['src'] == './Harbor%20commercial.mov'
    assert str(source) not in response.text


@pytest.mark.parametrize('clips', [[], [moment('missing', 2, 3)], [moment('1:1', 2, 5), moment('1:1', 2, 5)],
    [moment('1:1', 2, 121)], [moment('1:1', 3, 2)]])
def test_invalid_clip_selection_rejected_without_replacing_draft(client, asset, tmp_path, clips):
    job, draft, source = prepare(asset, tmp_path)
    ready(job, draft)
    fields = ('moment_id', 'start_seconds', 'end_seconds')
    result = client.patch(f'/api/library/{job.job_id}/rough-cut', json={'revision': draft['revision'],
        'clips': [{key: c[key] for key in fields} for c in clips]})
    assert result.status_code in (409, 422)
    assert get_job(job.job_id).rough_cut['revision'] == draft['revision']


def test_overlaps_and_changed_sources_block_exports(client, asset, tmp_path):
    job, draft, source = prepare(asset, tmp_path)
    ready(job, draft)
    first, second = moment('a', 2, 5), moment('b', 4, 8)
    with pytest.raises(ValueError, match='overlap'):
        rough_cut.validate_clips({'moments': [first, second]}, [first, second])
    source.write_bytes(b'new recording')
    response = client.get(f'/api/library/{job.job_id}/rough-cut/export', params={'revision': draft['revision'], 'format': 'json'})
    assert response.status_code == 409


def test_worker_saves_speech_before_selection_and_preserves_existing_analysis(asset, tmp_path):
    job, draft, source = prepare(asset, tmp_path)
    client = provider()
    media = Mock(get=AsyncMock(side_effect=lambda start, end: ('part', 'video/mp4', 0)), close=AsyncMock())
    with patch.object(rough_cut, 'get_client', return_value=client), patch.object(rough_cut, 'CutMedia', return_value=media):
        asyncio.run(rough_cut.build(job.job_id, None))
    restored = get_job(job.job_id)
    assert restored.rough_cut['status'] == 'ready', restored.rough_cut
    assert restored.rough_cut['completed_windows'] == 2
    assert restored.rough_cut['clips'][1]['start_seconds'] == 122
    assert len(restored.rough_cut['usage_records']) == 3
    assert restored.flash_result == job.flash_result
    media.close.assert_awaited_once()
    assert source.read_bytes() == b'original recording never modified'
    rough_cut._gate = None


def test_unknown_model_selection_fails_without_a_fake_ready_cut(asset, tmp_path):
    job, draft, source = prepare(asset, tmp_path, duration=120)
    client = provider()
    async def generate(**kwargs):
        if 'moments' in kwargs['config'].response_schema.properties:
            return SimpleNamespace(text=json.dumps({'moments': [{'start_time': '00:01', 'end_time': '00:05', 'text': 'Real speech'}]}), usage_metadata=None, candidates=[])
        return SimpleNamespace(text=json.dumps({'moment_ids': ['invented'], 'rationale': ''}), usage_metadata=None, candidates=[])
    client.aio.models.generate_content = AsyncMock(side_effect=generate)
    media = Mock(get=AsyncMock(return_value=('part', 'video/mp4', 0)), close=AsyncMock())
    with patch.object(rough_cut, 'get_client', return_value=client), patch.object(rough_cut, 'CutMedia', return_value=media):
        asyncio.run(rough_cut.build(job.job_id, None))
    saved = get_job(job.job_id).rough_cut
    assert saved['status'] == 'error' and saved['clips'] == []
    assert len(saved['moments']) == 1 and len(saved['usage_records']) == 2
    media.close.assert_awaited_once()
    rough_cut._gate = None


def test_cancellation_keeps_partial_evidence_and_cleans_up(asset, tmp_path):
    job, draft, source = prepare(asset, tmp_path)
    client = provider(); calls = 0
    original = client.aio.models.generate_content.side_effect
    async def generate(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2: raise asyncio.CancelledError
        return await original(**kwargs)
    client.aio.models.generate_content = AsyncMock(side_effect=generate)
    media = Mock(get=AsyncMock(return_value=('part', 'video/mp4', 0)), close=AsyncMock())
    with patch.object(rough_cut, 'get_client', return_value=client), patch.object(rough_cut, 'CutMedia', return_value=media):
        with pytest.raises(asyncio.CancelledError): asyncio.run(rough_cut.build(job.job_id, None))
    assert get_job(job.job_id).rough_cut['status'] == 'cancelled'
    assert len(get_job(job.job_id).rough_cut['moments']) == 1
    media.close.assert_awaited_once(); assert source.exists()
    rough_cut._gate = None


def test_later_upload_part_restores_speech_times_before_selecting(asset, tmp_path):
    job, draft, source = prepare(asset, tmp_path, duration=720)
    client = Mock()
    async def generate(**kwargs):
        if 'moments' in kwargs['config'].response_schema.properties:
            local = float(kwargs['contents'][0].video_metadata.start_offset[:-1])
            payload = {'moments': [{'start_time': str(local + 2), 'end_time': str(local + 5), 'text': 'Complete thought'}]}
        else:
            saved = get_job(job.job_id).rough_cut
            assert saved['completed_windows'] == 6
            assert saved['moments'][-1]['start_seconds'] == 602
            assert saved['moments'][-1]['start_time'] == '10:02.000'
            payload = {'moment_ids': ['6:1'], 'rationale': 'Final thought'}
        return SimpleNamespace(text=json.dumps(payload), usage_metadata=None, candidates=[])
    client.aio.models.generate_content = AsyncMock(side_effect=generate)
    media = Mock(get=AsyncMock(side_effect=lambda start, end: ('part', 'video/mp4', int(start // 600) * 600)), close=AsyncMock())
    with patch.object(rough_cut, 'get_client', return_value=client), patch.object(rough_cut, 'CutMedia', return_value=media):
        asyncio.run(rough_cut.build(job.job_id, None))
    assert get_job(job.job_id).rough_cut['status'] == 'ready'
    assert get_job(job.job_id).rough_cut['clips'][0]['start_seconds'] == 602
    rough_cut._gate = None
