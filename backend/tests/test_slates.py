"""Slate readings remain source-timed metadata, never technical facts or speech."""
import copy
import asyncio
import csv
import io
import json
import xml.etree.ElementTree as ET
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.models.analysis import Slate, SlateReading, Shot, SceneOutline
from app.services import slate_reader
from app.services.analysis_support import validate_shots
from app.services.flash_pass import run_shot_detection
from app.services.job_store import update_job
from app.services.library_export import NS, export_library
from app.services.shot_continuity import stitch_section


def reading(**values):
    return Slate(start_time='00:00.100', end_time='00:01.500', production_title='Orchard',
                 scene='012B', take='03', slate_shot='B-7', camera_roll='A001',
                 cinematographer='J. Doe', raw_text='ORCHARD\nSCENE 012B TAKE 03\nROLL A001', **values)


def shot(**values):
    return Shot(shot_number=1, start_time='00:00.000', end_time='00:30.000', shot_type='wide',
                camera_movement='static', visual_description='A slate, then an actor enters.',
                audio_notes='Clap', **values)


def test_slate_visible_intervals_are_validated_and_readings_stay_unreviewed():
    slate = reading(review_status='reviewed')
    invalid = reading().model_copy(update={'end_time': '00:31'})
    result = validate_shots([shot(slates=[slate, invalid])], 0, 30, 1)[0]
    assert len(result.slates) == 1
    assert result.slates[0].review_status == 'unreviewed'
    assert result.slates[0].scene == '012B' and result.slates[0].take == '03'
    assert result.shot_number == 1 and result.transcript == ''
    assert any('slate metadata' in w for w in result.analysis_warnings)


def test_continuous_take_keeps_a_head_slate_and_a_different_tail_slate():
    previous = shot(slates=[reading()])
    tail = Slate(start_time='00:34', end_time='00:35', scene='012B', take='04', raw_text='TAKE 04')
    current = shot(slates=[tail]).model_copy(update={'start_time': '00:30', 'end_time': '00:36', 'start_boundary': 'continuation'})
    owned, changed = stitch_section(previous, [current])
    assert owned == [] and changed[0].end_time == '00:36'
    assert [s.take for s in previous.slates] == ['03', '04']
    assert previous.slates[0].start_time == '00:00.100'


def test_proxy_slate_times_map_once_and_context_slate_is_discarded():
    slate = reading().model_copy(update={'start_time': '00:02.100', 'end_time': '00:03.500'})
    context = reading().model_copy(update={'start_time': '00:00', 'end_time': '00:01'})
    fragment = shot(slates=[slate, context]).model_copy(update={'start_time': '00:02', 'end_time': '00:32'})
    client = Mock()
    client.aio.models.generate_content = AsyncMock(return_value=SimpleNamespace(
        text=json.dumps({'shots': [fragment.model_dump()]}), candidates=[], usage_metadata=None))
    outline = SceneOutline(scene_number=21, scene_title='Section 21', scene_description='', start_time='10:00', end_time='10:30')
    with patch('app.services.flash_pass.get_client', return_value=client):
        shots, _ = asyncio.run(run_shot_detection('part', 'video/mp4', outline, 2, 1, source_offset=598, previous_shot=shot()))
    assert len(shots[0].slates) == 1
    assert shots[0].slates[0].start_time == '10:00.100'
    assert shots[0].slates[0].end_time == '10:01.500'


def test_slate_fields_search_and_all_metadata_exports_without_changing_source_facts(client, asset):
    flash = copy.deepcopy(asset.flash_result)
    flash['scenes'][0]['shots'][0]['slates'] = [reading().model_dump()]
    updated = update_job(asset.job_id, flash_result=flash)
    for query in ('012B', 'TAKE 03', 'A001', 'Orchard', 'Doe', 'B-7'):
        result = client.get('/api/library/search/shots', params={'job_id': asset.job_id, 'q': query}).json()
        assert result['total'] == 1
        assert result['shots'][0]['slates'][0]['scene'] == '012B'
    data = json.loads(export_library(updated, 'json', [1])[0])
    assert data['analysis']['flash_result']['scenes'][0]['shots'][0]['slates'][0]['take'] == '03'
    rows = list(csv.DictReader(io.StringIO(export_library(updated, 'csv', [1])[0].lstrip('\ufeff'))))
    assert rows[0]['Slate Scene'] == '012B' and rows[0]['Slate Camera Roll'] == 'A001'
    assert json.loads(rows[0]['Slate Observations'])[0]['slate_shot'] == 'B-7'
    assert None not in rows[0]
    xmp = ET.fromstring(export_library(updated, 'xmp', [1])[0])
    value = xmp.find('.//va:shotMetadata', NS).text
    assert json.loads(value)['slates'][0]['cinematographer'] == 'J. Doe'
    xml = ET.fromstring(export_library(updated, 'fcpxml', [1])[0])
    value = xml.find('.//asset-clip/metadata/md').get('value')
    assert json.loads(value)[0]['scene'] == '012B'
    assert updated.technical == asset.technical and updated.metadata == asset.metadata
    assert updated.transcript == asset.transcript


def test_legacy_analysis_and_unanalyzed_csv_remain_compatible(asset):
    assert shot().slates == []
    rows = list(csv.DictReader(io.StringIO(export_library(asset, 'csv')[0].lstrip('\ufeff'))))
    assert all(row['Slate Scene'] == '' and None not in row for row in rows)
    asset.flash_result = None
    rows = list(csv.DictReader(io.StringIO(export_library(asset, 'csv')[0].lstrip('\ufeff'))))
    assert len(rows) == 1 and None not in rows[0] and rows[0]['Slate Observations'] == ''


def test_invalid_board_crops_fall_back_to_the_complete_frame():
    for box in ([.2, .3, .4, .5], [0, 0, 0, .5], [.8, .3, .4, .5], [float('nan'), 0, .5, .5], [.2]):
        slate = reading().model_copy(update={'bounding_box': box})
        result = validate_shots([shot(slates=[slate])], 0, 30, 1)[0]
        assert len(result.slates) == 1
        assert result.slates[0].bounding_box == (box if box == [.2, .3, .4, .5] else [])


class SlateImageTests(unittest.IsolatedAsyncioTestCase):
    async def test_unreadable_still_keeps_the_video_reading(self):
        prior = shot(slates=[reading()])
        job = SimpleNamespace(local_path='original.mp4', viewing_path='')
        response = SimpleNamespace(text=SlateReading(notes='Board blurred.').model_dump_json(), candidates=[], usage_metadata=None)
        with patch.object(slate_reader, 'viewing_media', return_value='viewing.mp4'), \
             patch.object(slate_reader, 'slate_image', AsyncMock(return_value=b'frame')), \
             patch.object(slate_reader, 'get_client', return_value=Mock()), \
             patch.object(slate_reader, '_generate', AsyncMock(return_value=response)):
            await slate_reader.refine_slates(job, [prior])
        self.assertEqual(prior.slates[0].scene, '012B')
        self.assertTrue(prior.analysis_warnings)

    async def test_image_reading_corrects_fields_without_touching_timing_dialogue_or_source_metadata(self):
        slate = reading()
        prior = shot(slates=[slate], transcript='Action.')
        job = SimpleNamespace(local_path='original.mxf', viewing_path='viewing.mp4', technical={'source_timecode': '01:00:00:00'})
        response = SimpleNamespace(text=SlateReading(scene='012BA', take='04', camera_roll='A001 / B001',
            raw_text='SCENE 012BA TAKE 04 ROLL A001 / B001', review_status='reviewed').model_dump_json(), candidates=[], usage_metadata=None)
        usage = Mock()
        with patch.object(slate_reader, 'viewing_media', return_value='viewing.mp4'), \
             patch.object(slate_reader, 'slate_image', AsyncMock(return_value=b'frame')), \
             patch.object(slate_reader, 'get_client', return_value=Mock()), \
             patch.object(slate_reader, '_generate', AsyncMock(return_value=response)):
            await slate_reader.refine_slates(job, [prior], on_usage=usage)
        self.assertEqual(slate.scene, '012BA')
        self.assertEqual(slate.camera_roll, 'A001 / B001')
        self.assertEqual(slate.slate_shot, '')
        self.assertEqual(slate.review_status, 'unreviewed')
        self.assertEqual((slate.start_time, slate.end_time), ('00:00.100', '00:01.500'))
        self.assertEqual(prior.transcript, 'Action.')
        self.assertEqual(job.technical['source_timecode'], '01:00:00:00')
        self.assertEqual(usage.call_args.args[0]['stage'], 'slate_reading')

    async def test_failure_retains_video_reading_and_paid_usage_and_no_slate_costs_nothing(self):
        prior = shot(slates=[reading()])
        job = SimpleNamespace(local_path='original.mp4', viewing_path='')
        usage = Mock()
        with patch.object(slate_reader, 'viewing_media', return_value='viewing.mp4'), \
             patch.object(slate_reader, 'slate_image', AsyncMock(return_value=b'frame')), \
             patch.object(slate_reader, 'get_client', return_value=Mock()), \
             patch.object(slate_reader, '_generate', AsyncMock(return_value=SimpleNamespace(text='invalid', candidates=[], usage_metadata=None))) as generate:
            await slate_reader.refine_slates(job, [prior], on_usage=usage)
            self.assertEqual(prior.slates[0].scene, '012B')
            self.assertTrue(prior.analysis_warnings)
            self.assertEqual(usage.call_count, 1)
            await slate_reader.refine_slates(job, [shot()])
            self.assertEqual(generate.await_count, 1)

    async def test_frame_read_cancellation_reaps_ffmpeg(self):
        process = Mock(returncode=None)
        process.communicate = AsyncMock(side_effect=asyncio.CancelledError())
        process.wait = AsyncMock(return_value=-1)
        with patch.object(asyncio, 'create_subprocess_exec', AsyncMock(return_value=process)):
            with self.assertRaises(asyncio.CancelledError):
                await slate_reader.slate_image('original.mp4', reading())
        process.kill.assert_called_once()
        process.wait.assert_awaited_once()
