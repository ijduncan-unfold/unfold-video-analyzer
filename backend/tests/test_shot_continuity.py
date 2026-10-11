"""Editorial cuts, continuous takes, and preservation of source coordinates."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.config import settings
from app.models.analysis import Evidence, FlashAnalysis, SceneDeepAnalysis, Shot, VideoSummary
from app.services import analyzer, flash_pass
from app.services.analysis_support import format_time, processing_sections
from app.services.job_store import Job, create_job, get_job
from app.services.library_service import shot_views, match_shot
from app.services.library_export import export_library
from app.services.thumbnail_service import _thumbnail_time
from app.services.notes_synthesis import evidence_blocks


def fragment(start, end, boundary='unknown', number=1):
    return Shot(shot_number=number, start_time=format_time(start), end_time=format_time(end),
                start_boundary=boundary, shot_type='medium', camera_movement='static',
                visual_description='An empty corridor; two guards approach and a third figure passes the camera.',
                audio_notes='Footsteps', transcript=f'Words at {start}', tags=[f'action {start}'],
                evidence=[Evidence(description=f'Action at {start}', start_time=format_time(start),
                                   end_time=format_time(end))])


class ContinuousAnalysisTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.paths = patch.multiple(settings, database_path=str(Path(self.directory.name) / 'db.sqlite3'),
                                    upload_dir=str(Path(self.directory.name) / 'media'))
        self.paths.start()
        self.job = create_job(Job(job_id='continuous', filename='take.mp4', file_uri='uri', file_id='test', size_bytes=1,
            mime_type='video/mp4', local_path='original.mp4',
            technical={'duration_seconds': 62.375, 'has_audio': True, 'frame_rate_fraction': '24/1'}))

    def tearDown(self):
        self.paths.stop()
        self.directory.cleanup()

    async def analyze(self, detect, mode='flash_only'):
        async def deep(_uri, _mime, scene, **kwargs):
            return SceneDeepAnalysis(scene_number=scene.scene_number,
                visual_analysis={'lighting': f'Later lighting {scene.scene_number}'}), {}
        thumbs = AsyncMock()
        with patch.object(analyzer, 'run_shot_detection', side_effect=detect), \
             patch.object(analyzer, 'extract_thumbnails', thumbs), \
             patch.object(analyzer, 'run_pro_scene', side_effect=deep) as details, \
             patch.object(analyzer, 'run_summary_pass', AsyncMock(return_value=(VideoSummary(), {}))):
            events = [event async for event in analyzer.run_analysis(self.job, mode=mode)]
        return get_job(self.job.job_id), events, thumbs, details

    async def test_continuous_take_keeps_one_shot_and_all_timed_dialogue_and_section_notes(self):
        async def detect(_uri, _mime, section, number, fps, **kwargs):
            start = (section.scene_number - 1) * 30
            if start:
                self.assertEqual(kwargs['previous_shot'].start_time, '00:00.000')
                self.assertEqual(kwargs['previous_shot'].end_time, format_time(start))
            return [fragment(start, min(start + 30, 62.375), 'continuation' if start else 'unknown', number)], {}
        saved, events, thumbs, details = await self.analyze(detect, 'flash_pro')
        self.assertEqual(saved.status, 'complete')
        self.assertEqual(saved.flash_result['total_shots'], 1)
        shots = list(shot_views(saved))
        self.assertEqual(len(shots), 1)
        shot = shots[0]
        self.assertEqual((shot['start_time'], shot['end_time']), ('00:00.000', '01:02.375'))
        self.assertEqual(shot['section_numbers'], [1, 2, 3])
        self.assertEqual(len(shot['evidence']), 3)
        self.assertEqual([cue['shot_number'] for cue in saved.transcript], [1, 1, 1])
        self.assertEqual([cue['start_time'] for cue in saved.transcript], ['00:00.000', '00:30.000', '01:00.000'])
        self.assertEqual(shot['transcript'], 'Words at 0 Words at 30 Words at 60')
        self.assertEqual(details.call_count, 3)
        blocks = [json.loads(block) for block in evidence_blocks(FlashAnalysis(**saved.flash_result), saved.deep_results)]
        self.assertEqual([block['coverage'] for block in blocks if 'section' in block], ['analyzed'] * 3)
        self.assertGreater(match_shot('Later lighting 3', shot)[0], 0)
        midpoint = _thumbnail_time(thumbs.await_args_list[-1].args[2][0]['shots'][0])
        self.assertAlmostEqual(midpoint, 31.1875)
        checkpoints = [json.loads(e['data']) for e in events if e['event'] == 'section_complete']
        self.assertEqual([c['result']['total_shots'] for c in checkpoints], [1, 1, 1])
        self.assertTrue(all(c['successful'] for c in checkpoints))
        self.assertFalse(any('continuity will be checked' in w for w in saved.warnings))
        selected = json.loads(export_library(saved, 'json', [1])[0])
        self.assertEqual(len(selected['analysis']['deep_results']), 3)
        self.assertEqual(selected['analysis']['flash_result']['total_shots'], 1)
        self.assertEqual(len(selected['transcript']), 3)
        csv = export_library(saved, 'csv', [1])[0]
        self.assertIn('01:02.375', csv)

    async def test_actual_cut_at_boundary_is_kept_even_when_subject_and_framing_are_identical(self):
        async def detect(_uri, _mime, section, number, fps, **kwargs):
            start = (section.scene_number - 1) * 30
            return [fragment(start, min(start + 30, 62.375), 'cut', number)], {}
        saved, *_ = await self.analyze(detect)
        self.assertEqual(saved.flash_result['total_shots'], 3)
        self.assertEqual([s['shot_number'] for s in shot_views(saved)], [1, 2, 3])

    async def test_joined_interior_fragments_keep_dialogue_coordinates_across_sections(self):
        async def detect(_uri, _mime, section, number, fps, **kwargs):
            start = (section.scene_number - 1) * 30
            item = fragment(start, min(start + 30, 62.375), 'continuation' if start else 'unknown', number)
            if section.scene_number == 2:
                item._dialogue_fragments = [
                    {'start_time': '00:30.000', 'end_time': '00:40.000', 'text': 'Before the pan'},
                    {'start_time': '00:40.000', 'end_time': '01:00.000', 'text': 'After the pan'},
                ]
            return [item], {}
        saved, *_ = await self.analyze(detect)
        assert saved.flash_result['total_shots'] == 1
        assert [(cue['shot_number'], cue['start_time'], cue['end_time'], cue['text']) for cue in saved.transcript][1:3] == [
            (1, '00:30.000', '00:40.000', 'Before the pan'), (1, '00:40.000', '01:00.000', 'After the pan')]

    async def test_continuation_followed_by_an_interior_cut_preserves_correct_numbers(self):
        async def detect(_uri, _mime, section, number, fps, **kwargs):
            if section.scene_number == 1:
                return [fragment(0, 30)], {}
            if section.scene_number == 2:
                return [fragment(30, 40, 'continuation', number), fragment(40, 60, 'cut', number + 1)], {}
            return [fragment(60, 62.375, 'continuation', number)], {}
        saved, *_ = await self.analyze(detect)
        self.assertEqual([(s['shot_number'], s['start_time'], s['end_time']) for s in shot_views(saved)],
                         [(1, '00:00.000', '00:40.000'), (2, '00:40.000', '01:02.375')])
        self.assertEqual([cue['shot_number'] for cue in saved.transcript], [1, 1, 2, 2])

    async def test_unknown_boundaries_and_failed_coverage_are_never_merged(self):
        async def unknown(_uri, _mime, section, number, fps, **kwargs):
            start = (section.scene_number - 1) * 30
            return [fragment(start, min(start + 30, 62.375), 'unknown', number)], {}
        saved, *_ = await self.analyze(unknown)
        self.assertEqual(saved.flash_result['total_shots'], 3)
        async def gap(_uri, _mime, section, number, fps, **kwargs):
            if section.scene_number == 2:
                raise RuntimeError('provider failure')
            if section.scene_number == 3:
                self.assertNotIn('previous_shot', kwargs)
            start = (section.scene_number - 1) * 30
            return [fragment(start, min(start + 30, 62.375), 'continuation', number)], {}
        self.job = saved
        saved, *_ = await self.analyze(gap)
        self.assertEqual(saved.status, 'error')
        self.assertEqual(saved.flash_result['total_shots'], 2)
        self.assertEqual(len(saved.analysis_history), 1)


class BoundaryRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_lookback_is_context_only_and_part_offset_is_restored_once(self):
        prior = fragment(570, 600)
        current = fragment(2, 32, 'continuation')
        # Context observation must not leak into current-section evidence.
        current.evidence.append(Evidence(description='Context only', start_time='00:00', end_time='00:01'))
        client = Mock()
        client.aio.models.generate_content = AsyncMock(return_value=SimpleNamespace(
            text=json.dumps({'shots': [current.model_dump()]}), candidates=[], usage_metadata=None))
        outline = processing_sections(630)[20]
        with patch.object(flash_pass, 'get_client', return_value=client):
            shots, _ = await flash_pass.run_shot_detection('part', 'video/mp4', outline, 2, 1,
                                                         source_offset=598, previous_shot=prior)
        part, prompt = client.aio.models.generate_content.call_args.kwargs['contents']
        self.assertEqual(part.video_metadata.start_offset, '0s')
        self.assertEqual(part.video_metadata.end_offset, '32s')
        self.assertIn('look-back context', prompt)
        self.assertIn('WHOLE continuous take', prompt)
        self.assertNotIn('Words at 570', prompt)
        self.assertEqual(shots[0].start_time, '10:00.000')
        self.assertEqual(shots[0].end_time, '10:30.000')
        self.assertEqual(len(shots[0].evidence), 1)
        self.assertEqual(shots[0].evidence[0].start_time, '10:00.000')
