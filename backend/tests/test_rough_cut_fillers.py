import asyncio
import json
import xml.etree.ElementTree as ET
from fractions import Fraction
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.models.rough_cut import FillerSpan, RoughCutRequest
from app.services import rough_cut
from app.services.rough_cut_fillers import cleanup_rate, filler_windows, observed_fillers, remove_observed_fillers
from app.services.job_store import get_job, update_job
from test_rough_cut import prepare, moment


def test_option_defaults_off_and_round_trips_through_create_api(client, asset, tmp_path):
    job, draft, source = prepare(asset, tmp_path, duration=20)
    assert draft['remove_fillers'] is False and RoughCutRequest().remove_fillers is False
    update_job(job.job_id, rough_cut=None)
    with patch('app.routers.rough_cut.rough_cut.start'):
        response = client.post(f'/api/library/{job.job_id}/rough-cut',
            json={'remove_fillers': True}, headers={'X-API-Key': 'fake-test-key'})
    assert response.status_code == 202
    assert response.json()['remove_fillers'] is True
    assert get_job(job.job_id).rough_cut['remove_fillers'] is True
    assert source.read_bytes() == b'original recording never modified'


def test_detection_rejects_meaningful_words_and_bad_timing_and_restores_source_offset():
    samples = [FillerSpan(start_time='2', end_time='2.5', text=word) for word in ('umm', 'er', 'not', 'like', 'uh-huh')]
    samples += [FillerSpan(start_time='0', end_time='10', text='um'), FillerSpan(start_time='wrong', end_time='3', text='er')]
    accepted, skipped = observed_fillers(samples, 600, 601, 620)
    assert [span['text'] for span in accepted] == ['umm', 'er']
    assert accepted[0]['start_seconds'] == 602
    assert skipped == 5


def test_split_only_removes_fillers_and_preserves_context_and_original_moments():
    original = moment('1:1', 0, 10, 'An explanation, um, that keeps its meaning')
    draft = {'clips': [original], 'moments': [original]}
    spans = [{'start_seconds': 3, 'end_seconds': 3.5, 'text': 'um'},
             {'start_seconds': 3.1, 'end_seconds': 3.4, 'text': 'um'},
             {'start_seconds': 7, 'end_seconds': 7.25, 'text': 'er'}]
    clips, children, removed, skipped = remove_observed_fillers(draft, spans, Fraction(24))
    assert [(c['start_seconds'], c['end_seconds']) for c in clips] == [(0, 3), (3.5, 7), (7.25, 10)]
    assert len(children) == 3 and len(removed) == 2 and skipped == 0
    assert len({c['moment_id'] for c in clips}) == 3
    assert all(c['text_is_context'] and c['original_moment_id'] == '1:1' for c in clips)
    assert all(c['text'] == original['text'] for c in clips)
    assert draft['clips'] == [original] and draft['moments'] == [original]
    assert len(rough_cut.validate_clips({**draft, 'moments': [original, *children]}, clips)) == 3


def test_frame_snap_is_inward_and_uncertain_boundary_fragments_are_not_deleted():
    original = moment('a', 0, 5)
    clips, _, removed, _ = remove_observed_fillers({'clips': [original]},
        [{'start_seconds': 1.001, 'end_seconds': 1.499, 'text': 'uh'}], Fraction(24))
    assert removed[0]['start_seconds'] >= 1.001 and removed[0]['end_seconds'] <= 1.499
    assert clips[1]['start_seconds'] == pytest.approx(35 / 24)
    near_boundary = moment('a', .01, 5)
    clips, children, removed, skipped = remove_observed_fillers({'clips': [near_boundary]},
        [{'start_seconds': .04, 'end_seconds': .5, 'text': 'uh'}], Fraction(24))
    assert clips == [near_boundary] and not children and not removed and skipped == 1


def test_short_windows_only_cover_selected_speech_and_require_real_rate():
    assert filler_windows([moment('a', 2, 5), moment('b', 602, 605)], 720) == [(0, 20), (600, 620)]
    assert cleanup_rate({'frame_rate_fraction': '24000/1001'}) == Fraction(24000, 1001)
    with pytest.raises(ValueError, match='frame rate'):
        cleanup_rate({})


def test_worker_extra_pass_splits_excerpts_and_resolve_xml_has_contiguous_audio_timeline(client, asset, tmp_path):
    job, draft, source = prepare(asset, tmp_path, duration=20)
    update_job(job.job_id, rough_cut={**draft, 'remove_fillers': True})
    provider = Mock()
    async def reply(**kwargs):
        props = kwargs['config'].response_schema.properties
        if 'moments' in props:
            payload = {'moments': [{'start_time': '0', 'end_time': '10', 'text': 'A useful explanation with hesitations'}]}
        elif 'fillers' in props:
            assert 'Listen closely to the AUDIO' in kwargs['contents'][1]
            payload = {'fillers': [{'start_time': '3', 'end_time': '3.5', 'text': 'um'},
                                   {'start_time': '7', 'end_time': '7.25', 'text': 'er'}]}
        else:
            payload = {'moment_ids': ['1:1'], 'rationale': 'Keep the explanation'}
        return SimpleNamespace(text=json.dumps(payload), usage_metadata=None, candidates=[])
    provider.aio.models.generate_content = AsyncMock(side_effect=reply)
    media = Mock(get=AsyncMock(return_value=('part', 'video/mp4', 0)), close=AsyncMock())
    with patch.object(rough_cut, 'get_client', return_value=provider), patch.object(rough_cut, 'CutMedia', return_value=media):
        asyncio.run(rough_cut.build(job.job_id, None))
    saved = get_job(job.job_id).rough_cut
    assert saved['status'] == 'ready', saved
    assert saved['filler_removal']['removed_count'] == 2 and saved['duration_seconds'] == 9.25
    assert len(saved['usage_records']) == 3
    response = client.get(f'/api/library/{job.job_id}/rough-cut/export', params={'revision': saved['revision'], 'format': 'fcpxml'})
    assert response.status_code == 200, response.text
    xml = ET.fromstring(response.text)
    clips = xml.findall('.//spine/asset-clip')
    assert [c.attrib['start'] for c in clips] == ['0/24s', '84/24s', '174/24s']
    assert [c.attrib['offset'] for c in clips] == ['0/24s', '72/24s', '156/24s']
    assert xml.find('.//sequence').attrib['duration'] == '222/24s'
    assert xml.find('.//asset').attrib['hasAudio'] == '1'
    assert xml.find('.//media-rep').attrib['src'] == './Harbor%20commercial.mov'
    assert source.read_bytes() == b'original recording never modified'
    assert get_job(job.job_id).flash_result == job.flash_result
    media.close.assert_awaited_once()
    rough_cut._gate = None


def test_cleanup_restores_later_upload_times_and_never_crosses_part_boundary(asset, tmp_path):
    job, draft, source = prepare(asset, tmp_path, duration=720)
    draft.update(remove_fillers=True, status='assembling', clips=[moment('6:1', 602, 605)], moments=[moment('6:1', 602, 605)])
    update_job(job.job_id, rough_cut=draft)
    media = Mock(get=AsyncMock(return_value=('part', 'video/mp4', 598)))
    provider = Mock()
    provider.aio.models.generate_content = AsyncMock(return_value=SimpleNamespace(
        text=json.dumps({'fillers': [{'start_time': '5', 'end_time': '5.5', 'text': 'um'}]}), usage_metadata=None, candidates=[]))
    asyncio.run(rough_cut.clean_fillers(provider, media, job, draft))
    assert media.get.call_args.args == (600, 620.75)
    assert draft['filler_spans'][0]['start_seconds'] == 603
    assert [(clip['start_seconds'], clip['end_seconds']) for clip in draft['clips']] == [(602, 603), (603.5, 605)]
