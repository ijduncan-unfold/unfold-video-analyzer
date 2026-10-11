"""Feature-length routing, original timecodes and cleanup; no paid provider calls."""
import asyncio
import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.config import settings
from app.models.analysis import Evidence, FlashAnalysis, Scene, SceneDeepAnalysis, SceneOutline, Shot, VideoSummary
from app.models.library import AnalyzeRequest
from app.services import analyzer, flash_pass, job_runner, notes_synthesis, pro_pass, video_segments
from app.services.analysis_support import format_time, time_to_seconds
from app.services.job_store import Job, create_job, get_job


class LongVideoTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.source = self.root / 'original.mp4'
        self.source.write_bytes(b'original unchanged')
        self.paths = patch.multiple(settings, database_path=str(self.root / 'test.sqlite3'), upload_dir=str(self.root / 'uploads'))
        self.paths.start()
        self.job = create_job(Job(job_id='long-test', filename='Film.mp4', file_uri='whole-film-must-not-be-used',
            file_id='', mime_type='video/mp4', local_path=str(self.source), size_bytes=10,
            technical={'duration_seconds': 1800, 'has_audio': True}))

    def tearDown(self):
        self.paths.stop()
        self.directory.cleanup()

    def provider(self):
        client = Mock()
        client.aio.files.upload = AsyncMock(side_effect=[SimpleNamespace(name='part1'), SimpleNamespace(name='part2')])
        client.aio.files.delete = AsyncMock()
        return client

    def test_routing_preserves_short_video_and_youtube_paths(self):
        self.assertTrue(video_segments.needs_segments(self.job))
        self.job.technical['duration_seconds'] = 1799.999
        self.assertFalse(video_segments.needs_segments(self.job))
        self.job.size_bytes = 1024 ** 3 + 1
        self.assertTrue(video_segments.needs_segments(self.job))
        self.job.youtube_url = 'https://youtube.com/watch?v=test'
        self.assertFalse(video_segments.needs_segments(self.job))
        self.job.youtube_url = None
        self.job.technical = {}
        self.assertFalse(video_segments.needs_segments(self.job))

    async def test_fractional_final_part_ends_at_measured_film_duration(self):
        self.job.technical['duration_seconds'] = 1801.375
        async def encode(source, path, start, end):
            self.assertEqual((start, end), (1798, 1801.375))
            path.write_bytes(b'proxy')
        client = self.provider()
        with patch.object(video_segments, 'get_client', return_value=client), patch.object(video_segments, 'extract_segment', encode), patch.object(video_segments, 'poll_until_active', AsyncMock(return_value=SimpleNamespace(uri='tail'))):
            media = video_segments.SegmentedVideo(self.job)
            self.assertEqual(await media.get(1800, 1801.375), ('tail', 'video/mp4', 1798))
            self.assertEqual(get_job(self.job.job_id).analysis_progress['total_upload_parts'], 4)
            await media.close()

    async def test_encoder_cancellation_kills_and_reaps_process(self):
        proc = Mock(returncode=None)
        proc.communicate = AsyncMock(side_effect=asyncio.CancelledError)
        proc.wait = AsyncMock(return_value=-1)
        with patch.object(asyncio, 'create_subprocess_exec', AsyncMock(return_value=proc)):
            with self.assertRaises(asyncio.CancelledError):
                await video_segments.extract_segment(self.source, self.root / 'proxy.mp4', 0, 30)
        proc.kill.assert_called_once(); proc.wait.assert_awaited_once()
        self.assertEqual(self.source.read_bytes(), b'original unchanged')

    async def test_parts_are_lazy_reused_and_only_owned_proxy_and_uploads_are_removed(self):
        paths = []
        async def encode(source, path, start, end):
            self.assertEqual(source, str(self.source)); paths.append(path); path.write_bytes(b'proxy')
        client = self.provider()
        poll = AsyncMock(side_effect=lambda name, **kw: SimpleNamespace(uri='uri-' + name))
        with patch.object(video_segments, 'get_client', return_value=client), patch.object(video_segments, 'extract_segment', encode), patch.object(video_segments, 'poll_until_active', poll):
            media = video_segments.SegmentedVideo(self.job)
            self.assertEqual(client.aio.files.upload.await_count, 0)
            self.assertEqual(await media.get(0, 30), ('uri-part1', 'video/mp4', 0))
            self.assertEqual(await media.get(570, 600), ('uri-part1', 'video/mp4', 0))
            self.assertEqual(await media.get(600, 630), ('uri-part2', 'video/mp4', 598))
            self.assertEqual(client.aio.files.upload.await_count, 2)
            self.assertTrue(all(not p.exists() for p in paths))
            await media.close()
            self.assertEqual([c.kwargs['name'] for c in client.aio.files.delete.await_args_list], ['part1', 'part2'])
        self.assertEqual(self.source.read_bytes(), b'original unchanged')

    async def test_failed_part_is_not_uploaded_twenty_times_and_cleanup_keeps_original(self):
        client = self.provider()
        async def encode(source, path, start, end): path.write_bytes(b'proxy')
        with patch.object(video_segments, 'get_client', return_value=client), patch.object(video_segments, 'extract_segment', encode), patch.object(video_segments, 'poll_until_active', AsyncMock(side_effect=TimeoutError)):
            media = video_segments.SegmentedVideo(self.job)
            with self.assertRaises(TimeoutError): await media.get(0, 30)
            with self.assertRaises(RuntimeError): await media.get(30, 60)
            self.assertEqual(client.aio.files.upload.await_count, 1)
            await media.close()
            client.aio.files.delete.assert_awaited_once_with(name='part1')
        self.assertTrue(self.source.exists())

    async def test_cancellation_removes_the_temporary_proxy_and_registered_provider_part(self):
        paths = []
        async def encode(source, path, start, end): paths.append(path); path.write_bytes(b'proxy')
        client = self.provider()
        with patch.object(video_segments, 'get_client', return_value=client), patch.object(video_segments, 'extract_segment', encode), patch.object(video_segments, 'poll_until_active', AsyncMock(side_effect=asyncio.CancelledError)):
            media = video_segments.SegmentedVideo(self.job)
            with self.assertRaises(asyncio.CancelledError): await media.get(0, 30)
            await media.close()
        self.assertTrue(all(not p.exists() for p in paths)); self.assertTrue(self.source.exists())
        client.aio.files.delete.assert_awaited_once_with(name='part1')

    async def test_changed_source_and_cross_part_intervals_block_upload(self):
        media = video_segments.SegmentedVideo(self.job)
        with self.assertRaises(ValueError): await media.get(590, 610)
        self.source.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'changed'): await media.get(0, 30)

    async def test_runner_never_prepares_the_whole_feature_and_releases_parts(self):
        media = Mock(get=AsyncMock(), close=AsyncMock())
        async def fake_analysis(job, *args, **kwargs):
            self.assertIs(kwargs['media'], media)
            yield {'event': 'analysis_complete', 'data': '{}'}
        with patch.object(job_runner, 'SegmentedVideo', return_value=media), patch.object(job_runner, 'ensure_remote_file', AsyncMock()) as whole, patch.object(job_runner, 'run_analysis', fake_analysis):
            await job_runner._run(self.job.job_id, AnalyzeRequest(), None)
            whole.assert_not_awaited(); media.close.assert_awaited_once()
        job_runner._gate = None

    async def test_progressive_feature_uses_parts_for_both_passes_and_notes_for_global_requests(self):
        media = Mock()
        media.get = AsyncMock(side_effect=lambda start, end: ('part-uri', 'video/mp4', float(int(start // 600) * 600)))
        async def shots(uri, mime, outline, number, fps, **kwargs):
            return [Shot(shot_number=number, start_time=outline.start_time, end_time=outline.end_time,
                shot_type='wide', camera_movement='static', visual_description='Visual evidence', audio_notes='')], {'model': 'test', 'usage_available': False}
        async def deep(uri, mime, scene, **kwargs): return SceneDeepAnalysis(scene_number=scene.scene_number), {'model': 'test', 'usage_available': False}
        synth = AsyncMock(side_effect=[(VideoSummary(executive_summary='Film beats'), []), ({'trailer_moments': []}, [])])
        self.job.local_path = ''  # No thumbnail work needed in this routing test.
        with patch.object(analyzer, 'run_shot_detection', AsyncMock(side_effect=shots)) as shot_pass, patch.object(analyzer, 'run_pro_scene', AsyncMock(side_effect=deep)) as deep_pass, patch.object(analyzer, 'synthesize_notes', synth), patch.object(analyzer, 'run_summary_pass', AsyncMock()) as whole_summary, patch.object(analyzer, 'run_custom_pass', AsyncMock()) as whole_custom:
            events = []
            async for event in analyzer.run_analysis(self.job, media=media, custom_prompt='Find trailer moments'):
                events.append(event['event'])
                if event['event'] == 'section_complete' and events.count('section_complete') == 1:
                    self.assertEqual(get_job(self.job.job_id).flash_result['total_shots'], 1)
                    self.assertEqual(shot_pass.await_count, 1)
            self.assertEqual(shot_pass.await_count, 60); self.assertEqual(deep_pass.await_count, 60)
            self.assertEqual(shot_pass.await_args_list[20].kwargs['source_offset'], 600)
            whole_summary.assert_not_awaited(); whole_custom.assert_not_awaited()
            self.assertEqual(synth.await_count, 2)
        saved = get_job(self.job.job_id)
        self.assertEqual(saved.status, 'complete')
        self.assertEqual(saved.analysis_config['upload_strategy'], 'segmented_proxy')
        self.assertEqual(saved.flash_result['scenes'][-1]['shots'][0]['end_time'], '30:00.000')

    async def test_late_film_shot_and_evidence_are_translated_exactly_once(self):
        shot = Shot(shot_number=1, start_time='00:12.375', end_time='00:13.625', shot_type='close-up',
            camera_movement='static', visual_description='Circle', audio_notes='', transcript='Hello',
            evidence=[Evidence(description='Circle', start_time='00:12.500', end_time='00:13.500')])
        client = Mock(); client.aio.models.generate_content = AsyncMock(return_value=SimpleNamespace(text=json.dumps({'shots': [shot.model_dump()]}), usage_metadata=None, candidates=[]))
        outline = SceneOutline(scene_number=121, scene_title='Section 121', scene_description='', start_time='01:00:12.375', end_time='01:00:13.625')
        with patch.object(flash_pass, 'get_client', return_value=client):
            result, _ = await flash_pass.run_shot_detection('part', 'video/mp4', outline, 321, 1, source_offset=3600)
        request = client.aio.models.generate_content.call_args.kwargs
        self.assertEqual(request['contents'][0].video_metadata.start_offset, '12.375s')
        self.assertEqual(result[0].shot_number, 321)
        self.assertEqual(result[0].start_time, '01:00:12.375')
        self.assertEqual(result[0].evidence[0].end_time, '01:00:13.500')

    async def test_deep_evidence_is_validated_in_part_time_then_restored_to_film_time(self):
        result = SceneDeepAnalysis(scene_number=1, evidence=[Evidence(description='Person', start_time='00:01', end_time='00:02')])
        client = Mock(); client.aio.models.generate_content = AsyncMock(return_value=SimpleNamespace(text=result.model_dump_json(), usage_metadata=None, candidates=[]))
        scene = Scene(scene_number=121, scene_title='Section', scene_description='', start_time='01:00:00', end_time='01:00:30')
        with patch.object(pro_pass, 'get_client', return_value=client):
            deep, _ = await pro_pass.run_pro_scene('part', 'video/mp4', scene, source_offset=3600)
        self.assertEqual(deep.evidence[0].start_time, '01:00:01.000')
        self.assertEqual(deep.scene_number, 121)

    async def test_hierarchical_summary_bounds_each_request_and_keeps_all_evidence_inputs(self):
        shots = [Shot(shot_number=i, start_time=format_time(i), end_time=format_time(i + 1), shot_type='wide',
            camera_movement='static', visual_description='x' * 20000, audio_notes='', transcript=f'Unique dialogue {i}') for i in range(1, 6)]
        flash = FlashAnalysis(total_duration='02:00:00', total_shots=5, total_scenes=1,
            scenes=[Scene(scene_number=1, scene_title='Section', scene_description='', start_time='00:00', end_time='00:30', shots=shots)])
        client = Mock()
        async def generate(**kwargs):
            schema = kwargs['config'].response_schema
            payload = {'notes': 'Source Shot 1 at 00:01: Unique dialogue 1'} if 'notes' in schema.properties else {'executive_summary': 'Evidence-based film summary'}
            return SimpleNamespace(text=json.dumps(payload), usage_metadata=None, candidates=[])
        client.aio.models.generate_content = AsyncMock(side_effect=generate)
        with patch.object(notes_synthesis, 'get_client', return_value=client):
            result, records = await notes_synthesis.synthesize_notes(flash, [])
        calls = client.aio.models.generate_content.await_args_list
        self.assertGreater(len(calls), 1); self.assertEqual(len(records), len(calls))
        all_inputs = '\n'.join(c.kwargs['contents'][0] for c in calls)
        for i in range(1, 6): self.assertIn(f'Unique dialogue {i}', all_inputs)
        self.assertTrue(all(len(c.kwargs['contents'][0]) < 50000 for c in calls))
        self.assertEqual(result.total_runtime, '02:00:00'); self.assertEqual(result.total_shots, 5)

    async def test_synthesis_usage_is_recorded_even_when_response_is_invalid(self):
        flash = FlashAnalysis(total_duration='02:00:00', total_shots=1, total_scenes=1,
            scenes=[Scene(scene_number=1, scene_title='Section', scene_description='',
                start_time='00:00', end_time='00:30')])
        client = Mock()
        client.aio.models.generate_content = AsyncMock(return_value=SimpleNamespace(
            text='invalid json', usage_metadata=None, candidates=[]))
        recorded = []
        with patch.object(notes_synthesis, 'get_client', return_value=client):
            with self.assertRaises(ValueError):
                await notes_synthesis.synthesize_notes(flash, [], on_usage=recorded.append)
        self.assertEqual(len(recorded), 1)


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='FFmpeg required')
def test_real_proxy_preserves_source_bytes_and_audio_and_resets_part_time(tmp_path):
    source, proxy = tmp_path / 'original.mp4', tmp_path / 'part.mp4'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=24:duration=6',
        '-f', 'lavfi', '-i', 'sine=frequency=440:duration=6', '-c:v', 'libx264', '-threads', '2', '-c:a', 'aac', '-shortest', str(source)], check=True)
    identity = source.stat(); digest = hashlib.sha256(source.read_bytes()).digest()
    asyncio.run(video_segments.extract_segment(source, proxy, 2, 4))
    facts = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(proxy)]))
    assert abs(float(facts['format']['duration']) - 2) < 0.1
    assert any(s['codec_type'] == 'audio' for s in facts['streams'])
    video = next(s for s in facts['streams'] if s['codec_type'] == 'video')
    assert abs(float(video['start_time'])) < 0.01
    assert video['width'] <= 1280 and video['height'] <= 720
    assert source.stat().st_mtime_ns == identity.st_mtime_ns
    assert hashlib.sha256(source.read_bytes()).digest() == digest
