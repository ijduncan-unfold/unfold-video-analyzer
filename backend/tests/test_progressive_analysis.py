"""Progress visibility and failure semantics without media, network, or the real DB."""
import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from app.config import settings
from app.models.analysis import SceneDetectionResult, Shot
from app.services import analyzer, flash_pass
from app.services.analysis_support import processing_sections, time_to_seconds
from app.services.job_store import Job, create_job, get_job, update_job
from app.services.library_service import thumbnail_url


def shot_for(section, offset=1, start=None):
    return Shot(shot_number=offset, start_time=start or section.start_time,
                end_time=section.end_time, shot_type="wide", camera_movement="static",
                visual_description="A colored object", audio_notes="")


def usage():
    return {"model": "gemini-3.8-flash", "stage": "shot_analysis", "input_tokens": 100,
            "output_tokens": 30, "thinking_tokens": 10, "usage_available": True}


class SectionTests(unittest.TestCase):
    def test_sections_cover_152_second_source_with_bounded_fractional_tail(self):
        sections = processing_sections(152.3759)
        spans = [(time_to_seconds(s.start_time), time_to_seconds(s.end_time)) for s in sections]
        self.assertEqual(spans, [(0, 30), (30, 60), (60, 90), (90, 120), (120, 150), (150, 152.375)])
        self.assertTrue(all(0 < end - start <= 30 for start, end in spans))
        self.assertTrue(all(s.scene_title == f"Section {i + 1}" for i, s in enumerate(sections)))
        self.assertEqual(len(processing_sections(60)), 2)
        for value in (0, -1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                processing_sections(value)


class ProgressiveAnalysisTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.paths = patch.multiple(settings,
            database_path=str(Path(self.directory.name) / "isolated.sqlite3"),
            upload_dir=str(Path(self.directory.name) / "media"))
        self.paths.start()
        self.job = create_job(Job(job_id="progressive-test", file_id="test-provider-file", file_uri="uri",
            mime_type="video/mp4", filename="test.mp4", size_bytes=1, local_path="local-test-only.mp4",
            technical={"duration_seconds": 62.375, "has_audio": False}, metadata={"client": "Human client"}))

    def tearDown(self):
        self.paths.stop()
        self.directory.cleanup()

    def prior_result_with_thumbnail(self):
        section = processing_sections(30)[0]
        old_config = {"started_at": "2020-01-01T00:00:00+00:00", "analysis_model": "prior-model", "fps": 4}
        self.job = update_job(self.job.job_id, technical={"duration_seconds": 60},
            flash_result={"total_shots": 1, "scenes": [{**section.model_dump(),
                          "shots": [shot_for(section).model_dump()]}]}, analysis_config=old_config,
            shot_annotations={"1": {"notes": "Reviewed previous result"}}, analysis_history=[])
        thumb = Path(settings.upload_dir) / self.job.job_id / "thumbs" / "shot_1.jpg"
        thumb.parent.mkdir(parents=True, exist_ok=True)
        thumb.write_bytes(b"previous thumbnail")
        timestamp = datetime.fromisoformat(old_config["started_at"]).timestamp() + 1
        os.utime(thumb, (timestamp, timestamp))
        return old_config, thumbnail_url(self.job, 1)

    async def test_attempt_without_positive_replacement_retains_previous_provenance_and_thumbnails(self):
        for failure in (RuntimeError("provider failure"), asyncio.CancelledError()):
            with self.subTest(failure=type(failure).__name__):
                old_config, old_url = self.prior_result_with_thumbnail()
                self.assertIsNotNone(old_url)
                with patch.object(analyzer, "run_shot_detection", AsyncMock(side_effect=failure)):
                    if isinstance(failure, asyncio.CancelledError):
                        with self.assertRaises(asyncio.CancelledError):
                            _ = [e async for e in analyzer.run_analysis(self.job, mode="flash_only")]
                    else:
                        _ = [e async for e in analyzer.run_analysis(self.job, mode="flash_only")]
                saved = get_job(self.job.job_id)
                self.assertEqual(saved.analysis_config, old_config)
                self.assertEqual(saved.flash_result, self.job.flash_result)
                self.assertEqual(saved.shot_annotations, self.job.shot_annotations)
                self.assertEqual(saved.analysis_history, [])
                self.assertEqual(thumbnail_url(saved, 1), old_url)
                self.assertEqual(saved.analysis_progress["completed_shots"], 0)
                self.assertNotEqual(saved.analysis_progress["started_at"], old_config["started_at"])

    async def test_provenance_and_thumbnail_gate_switch_at_first_positive_snapshot(self):
        old_config, old_url = self.prior_result_with_thumbnail()
        checked_new_snapshot = False

        async def detect(_uri, _mime, section, offset, _fps, **_kwargs):
            current = get_job(self.job.job_id)
            if section.scene_number == 1:
                self.assertEqual(current.analysis_config, old_config)
                self.assertEqual(thumbnail_url(current, 1), old_url)
                return [shot_for(section, offset, start="00:00.500")], usage()
            return [shot_for(section, offset)], usage()

        async def thumbnail_checkpoint(*_args):
            nonlocal checked_new_snapshot
            current = get_job(self.job.job_id)
            if not checked_new_snapshot:
                self.assertEqual(current.flash_result["total_shots"], 1)
                self.assertEqual(current.flash_result["scenes"][0]["shots"][0]["start_time"], "00:00.500")
                self.assertNotEqual(current.analysis_config["started_at"], old_config["started_at"])
                self.assertEqual(current.analysis_config["fps"], 1)
                self.assertEqual(current.analysis_config["started_at"], current.analysis_progress["started_at"])
                self.assertIsNone(thumbnail_url(current, 1))
                self.assertEqual(current.analysis_history[0]["analysis_config"], old_config)
                checked_new_snapshot = True

        with patch.object(analyzer, "run_shot_detection", side_effect=detect), \
             patch.object(analyzer, "extract_thumbnails", side_effect=thumbnail_checkpoint):
            _ = [e async for e in analyzer.run_analysis(self.job, mode="flash_only")]
        self.assertTrue(checked_new_snapshot)
        self.assertEqual(get_job(self.job.job_id).status, "complete")

    async def test_first_section_and_thumbnails_visible_while_next_request_is_waiting_then_cancel(self):
        second_started, provider_cancelled = asyncio.Event(), asyncio.Event()
        seen = []

        async def detect(_uri, _mime, section, offset, _fps, **_kwargs):
            if section.scene_number == 2:
                second_started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    provider_cancelled.set()
            return [shot_for(section, offset)], usage()

        async def consume():
            async for event in analyzer.run_analysis(self.job, mode="flash_only"):
                seen.append(event)

        thumbs = AsyncMock(return_value="isolated-thumbnails")
        with patch.object(analyzer, "run_scene_detection", AsyncMock()) as scene_gate, \
             patch.object(analyzer, "run_shot_detection", side_effect=detect), \
             patch.object(analyzer, "extract_thumbnails", thumbs):
            task = asyncio.create_task(consume())
            try:
                await asyncio.wait_for(second_started.wait(), 2)
                partial = get_job(self.job.job_id)
                self.assertEqual(partial.status, "analyzing")
                self.assertEqual(partial.flash_result["total_shots"], 1)
                self.assertEqual(partial.flash_result["grouping"], "processing_sections")
                self.assertEqual(partial.analysis_progress["completed_sections"], 1)
                self.assertEqual(partial.analysis_progress["processed_seconds"], 30)
                self.assertEqual(partial.analysis_progress["total_sections"], 3)
                self.assertEqual(thumbs.await_count, 1)
                self.assertEqual(len(thumbs.call_args.args[2]), 1)
                scene_gate.assert_not_awaited()
                self.assertTrue(any(e["event"] == "thumbnails_ready" for e in seen))
                self.assertTrue(any(e["event"] == "section_complete" for e in seen))
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        self.assertTrue(provider_cancelled.is_set())
        saved = get_job(self.job.job_id)
        self.assertEqual(saved.flash_result["total_shots"], 1)
        self.assertEqual(saved.analysis_progress["stage"], "cancelled")
        self.assertEqual(saved.analysis_progress["processed_seconds"], 30)

    async def test_failed_middle_window_keeps_other_shots_and_reports_only_successful_coverage(self):
        async def detect(_uri, _mime, section, offset, _fps, **_kwargs):
            if section.scene_number == 2:
                raise RuntimeError("private-url?key=DO_NOT_SAVE")
            return [shot_for(section, offset)], usage()

        with patch.object(analyzer, "run_shot_detection", side_effect=detect), \
             patch.object(analyzer, "extract_thumbnails", AsyncMock()):
            events = [e async for e in analyzer.run_analysis(self.job, mode="flash_only")]
        saved = get_job(self.job.job_id)
        self.assertEqual(saved.status, "error")
        self.assertEqual(saved.analysis_progress["completed_sections"], 2)
        self.assertEqual(saved.analysis_progress["failed_sections"], 1)
        self.assertEqual(saved.analysis_progress["processed_seconds"], 32.375)
        self.assertEqual(saved.analysis_progress["completed_shots"], 2)
        self.assertEqual([s["shot_number"] for c in saved.flash_result["scenes"] for s in c["shots"]], [1, 2])
        self.assertEqual(saved.flash_result["scenes"][1]["shots"], [])
        self.assertEqual(saved.flash_result["scenes"][2]["shots"][0]["start_time"], "01:00.000")
        self.assertEqual(sum(e["event"] == "section_complete" for e in events), 3)
        self.assertFalse(any(e["event"] == "analysis_complete" for e in events))
        self.assertNotIn("DO_NOT_SAVE", json.dumps(events) + json.dumps(saved.warnings) + saved.error)
        self.assertTrue(any('Shot analysis for section 2' in warning for warning in saved.warnings))

    async def test_failed_initial_checkpoint_on_new_asset_is_not_archived_as_an_older_run(self):
        async def detect(_uri, _mime, section, offset, _fps, **_kwargs):
            if section.scene_number == 1:
                raise RuntimeError("first window failed")
            return [shot_for(section, offset)], usage()

        with patch.object(analyzer, "run_shot_detection", side_effect=detect), \
             patch.object(analyzer, "extract_thumbnails", AsyncMock()):
            _ = [e async for e in analyzer.run_analysis(self.job, mode="flash_only")]
        saved = get_job(self.job.job_id)
        self.assertEqual(saved.analysis_progress["completed_shots"], 2)
        self.assertEqual(saved.analysis_history, [])
        self.assertEqual(saved.analysis_config["grouping"], "processing_sections")

    async def test_reanalysis_restores_later_unchanged_reviews_without_reusing_changed_ones(self):
        sections = processing_sections(60)
        old_scenes = [{**section.model_dump(), "shots": [shot_for(section, i + 1).model_dump()]}
                      for i, section in enumerate(sections)]
        self.job = update_job(self.job.job_id, technical={"duration_seconds": 60},
            flash_result={"total_shots": 2, "scenes": old_scenes},
            shot_annotations={"1": {"tags": ["old-first"]}, "2": {"tags": ["keep-second"]}},
            analysis_progress={"stage": "complete", "completed_shots": 2},
            analysis_config={"model": "prior-run"})

        async def detect(_uri, _mime, section, offset, _fps, **_kwargs):
            if section.scene_number == 1:
                return [shot_for(section, offset, start="00:00.500")], usage()
            partial = get_job(self.job.job_id)
            self.assertEqual(partial.shot_annotations, {})
            self.assertEqual(len(partial.analysis_history), 1)
            update_job(self.job.job_id, shot_annotations={"1": {"notes": "Reviewed the NEW shot"}},
                       metadata={"client": "Edited during indexing"})
            return [shot_for(section, offset)], usage()

        with patch.object(analyzer, "run_shot_detection", side_effect=detect), \
             patch.object(analyzer, "extract_thumbnails", AsyncMock()):
            _ = [e async for e in analyzer.run_analysis(self.job, mode="flash_only")]
        saved = get_job(self.job.job_id)
        self.assertEqual(saved.status, "complete")
        self.assertEqual(saved.shot_annotations, {"1": {"notes": "Reviewed the NEW shot"},
                                                "2": {"tags": ["keep-second"]}})
        self.assertEqual(saved.metadata["client"], "Edited during indexing")
        self.assertEqual(saved.analysis_history[0]["shot_annotations"]["1"]["tags"], ["old-first"])
        self.assertNotIn("analysis_progress", saved.analysis_history[0])
        self.assertEqual(saved.analysis_progress["completed_sections"], 2)

    async def test_unknown_duration_uses_estimated_fallback_then_bounded_sections(self):
        self.job = update_job(self.job.job_id, technical={})
        outline = processing_sections(62.375, window_seconds=90)[0]
        result = SceneDetectionResult(total_duration="01:02.375", total_scenes=1, scenes=[outline])
        spans = []

        async def detect(_uri, _mime, section, offset, _fps, **_kwargs):
            spans.append((section.start_time, section.end_time))
            return [shot_for(section, offset)], usage()

        with patch.object(analyzer, "run_scene_detection", AsyncMock(return_value=(result, usage()))) as fallback, \
             patch.object(analyzer, "run_shot_detection", side_effect=detect), \
             patch.object(analyzer, "extract_thumbnails", AsyncMock()):
            _ = [e async for e in analyzer.run_analysis(self.job, mode="flash_only")]
        fallback.assert_awaited_once()
        self.assertEqual(len(spans), 3)
        saved = get_job(self.job.job_id)
        self.assertEqual(saved.status, "complete")
        self.assertEqual(saved.analysis_config["duration_source"], "model_estimate")
        self.assertTrue(any("AI duration estimate" in w for w in saved.warnings))


class ProviderDeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_bounded_request_cancels_transport_on_deadline(self):
        cancelled = asyncio.Event()

        async def hang(**_kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        fake = Mock()
        fake.aio.models.generate_content = AsyncMock(side_effect=hang)
        with patch.object(flash_pass, "get_client", return_value=fake), \
             patch.object(flash_pass, "REQUEST_TIMEOUT_SECONDS", .01), \
             patch('app.services.provider_retry.asyncio.sleep', AsyncMock()):
            with self.assertRaises(TimeoutError):
                await flash_pass.run_shot_detection("uri", "video/mp4", processing_sections(2)[0], 1, 1)
        self.assertTrue(cancelled.is_set())
        self.assertEqual(fake.aio.models.generate_content.await_count, 3)
