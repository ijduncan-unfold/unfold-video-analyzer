"""Reframing is not an edit; real cuts and uncertain coverage stay separate."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.models.analysis import Evidence, Shot, Slate
from app.services import flash_pass
from app.services.analysis_support import format_time, processing_sections
from app.services.shot_boundary_review import review_boundaries, UNCONFIRMED


def shot(number, start, end, description='Corridor', boundary='cut', speech=''):
    result = Shot(shot_number=number, start_time=format_time(start), end_time=format_time(end),
        shot_type='medium', camera_movement='static', visual_description=description,
        audio_notes='', transcript=speech, start_boundary=boundary,
        evidence=[Evidence(description=description, start_time=format_time(start), end_time=format_time(end))])
    if speech:
        result._dialogue_fragments = [{'start_time': result.start_time, 'end_time': result.end_time, 'text': speech}]
    return result


def response(boundaries):
    return SimpleNamespace(text=json.dumps({'boundaries': boundaries}), candidates=[], usage_metadata=None)


def review(number, decision='continuation'):
    return {'shot_number': number, 'decision': decision, 'observation': 'The wall moves continuously across the frame.',
        'continuation_description': 'The camera pans across the same corridor into darkness.', 'camera_movement': 'pan'}


def test_pan_into_darkness_joins_but_retains_timed_speech_and_slate():
    first, second = shot(1, 60, 80.916, speech='Action'), shot(2, 80.916, 82.916, 'Black screen after cut', speech='Cut')
    second.slates = [Slate(start_time='01:21.000', end_time='01:22.000', scene='9A')]
    generate = AsyncMock(return_value=response([review(2)]))
    joined, records = asyncio.run(review_boundaries(Mock(), generate, 'uri', 'video/mp4', [first, second], 60, 82.916))
    assert len(joined) == 1 and joined[0].end_time == '01:22.916'
    assert 'Black screen after cut' not in joined[0].visual_description
    assert joined[0].camera_movement == 'static / pan'
    assert joined[0].transcript == 'Action Cut'
    assert [cue['start_time'] for cue in joined[0]._dialogue_fragments] == ['01:00.000', '01:20.916']
    assert len(joined[0].evidence) == 2 and joined[0].slates[0].scene == '9A'
    request = generate.call_args.kwargs
    part = request['contents'][0]
    assert part.video_metadata.start_offset == '78.916s'
    assert part.video_metadata.end_offset == '82.916s'
    assert part.video_metadata.fps == 8 and records[0]['stage'] == 'shot_boundary_review'


@pytest.mark.parametrize('decision', ['cut', 'unknown'])
def test_real_cut_or_uncertain_motion_keeps_even_identical_subjects_separate(decision):
    items = [shot(1, 0, 10), shot(2, 10, 20)]
    result, _ = asyncio.run(review_boundaries(Mock(), AsyncMock(return_value=response([review(2, decision)])),
        'uri', 'video/mp4', items, 0, 20))
    assert len(result) == 2
    if decision == 'unknown':
        assert UNCONFIRMED in result[1].analysis_warnings


@pytest.mark.parametrize('boundaries', [[], [review(2), review(2)], [review(99)], [{**review(2), 'observation': ''}]])
def test_incomplete_duplicate_or_unsubstantiated_review_does_not_merge(boundaries):
    result, records = asyncio.run(review_boundaries(Mock(), AsyncMock(return_value=response(boundaries)),
        'uri', 'video/mp4', [shot(1, 0, 10), shot(2, 10, 20, boundary='continuation')], 0, 20))
    assert len(result) == 2 and UNCONFIRMED in result[1].analysis_warnings
    assert len(records) == 1  # The successful provider call is still billed.


def test_failed_review_retains_split_and_cancellation_propagates():
    async def check(error):
        return await review_boundaries(Mock(), AsyncMock(side_effect=error), 'uri', 'video/mp4',
            [shot(1, 0, 10), shot(2, 10, 20)], 0, 20)
    result, _ = asyncio.run(check(TimeoutError()))
    assert len(result) == 2 and UNCONFIRMED in result[1].analysis_warnings
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(check(asyncio.CancelledError()))


def test_missing_coverage_is_never_joined_or_reviewed():
    generate = AsyncMock()
    result, _ = asyncio.run(review_boundaries(Mock(), generate, 'uri', 'video/mp4',
        [shot(1, 0, 9), shot(2, 10, 20, boundary='continuation')], 0, 20))
    assert len(result) == 2
    generate.assert_not_awaited()


def test_reviews_are_batched_without_exceeding_video_limit():
    generate = AsyncMock(side_effect=[response([review(i, 'cut')]) for i in range(2, 12)])
    result, _ = asyncio.run(review_boundaries(Mock(), generate, 'uri', 'video/mp4',
        [shot(i + 1, i * 2, (i + 1) * 2) for i in range(11)], 0, 22))
    assert len(result) == 11 and generate.await_count == 10
    assert [len(call.kwargs['contents']) - 1 for call in generate.await_args_list] == [1] * 10


def test_uploaded_part_offset_and_billed_usage_applied_exactly_once():
    parts = [shot(20, 2, 20, speech='Earlier'), shot(21, 20, 32, speech='Later')]
    usage = SimpleNamespace(prompt_token_count=10, candidates_token_count=5, thoughts_token_count=2,
                            cached_content_token_count=0, total_token_count=17)
    detection = SimpleNamespace(text=json.dumps({'shots': [s.model_dump() for s in parts]}), candidates=[], usage_metadata=usage)
    verified = response([review(21)]); verified.usage_metadata = usage
    client = Mock(); client.aio.models.generate_content = AsyncMock(side_effect=[detection, verified])
    with patch.object(flash_pass, 'get_client', return_value=client):
        result, billed = asyncio.run(flash_pass.run_shot_detection('part', 'video/mp4',
            processing_sections(630)[20], 20, 1, source_offset=598))
    assert [(s.start_time, s.end_time) for s in result] == [('10:00.000', '10:30.000')]
    assert [cue['start_time'] for cue in result[0]._dialogue_fragments] == ['10:00.000', '10:18.000']
    assert billed['input_tokens'] == 20 and billed['output_tokens'] == 14 and billed['total_tokens'] == 34
    assert len(billed['boundary_reviews']) == 1


def test_processing_boundary_review_uses_prior_film_coordinates_only_for_adjacency():
    prior = shot(19, 570, 600)
    generate = AsyncMock(return_value=response([review(20)]))
    result, _ = asyncio.run(review_boundaries(Mock(), generate, 'uri', 'video/mp4',
        [shot(20, 2, 32)], 2, 32, prior, source_offset=598))
    assert len(result) == 1 and result[0].start_boundary == 'continuation'
    assert generate.call_args.kwargs['contents'][0].video_metadata.start_offset == '0s'
    assert prior.end_time == '10:00.000'  # Cross-section stitching happens later.
