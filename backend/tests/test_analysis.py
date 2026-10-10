import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.config import settings
from app.models.analysis import (
    Evidence, SceneDetectionResult, SceneOutline, Shot, SceneDeepAnalysis, VideoSummary,
)
from app.services import analyzer
from app.services.analysis_support import (
    clip_offset, extract_usage, format_time, response_text, time_to_seconds,
    validate_evidence, validate_scenes, validate_shots,
)
from app.services.cost_estimator import estimate_cost
from app.services.flash_pass import run_shot_detection
from app.services.job_store import Job, create_job, get_job, update_job


def shot(start="00:00.000", end="00:02.000", **values):
    return Shot(shot_number=1, start_time=start, end_time=end, shot_type="close-up",
                camera_movement="static", visual_description="A cup on a table", audio_notes="",
                **values)


def scenes():
    return SceneDetectionResult(total_duration="00:02.000", total_scenes=1, scenes=[
        SceneOutline(scene_number=1, scene_title="Product", scene_description="A product shot",
                     start_time="00:00.000", end_time="00:02.000"),
    ])


def usage(stage="shot_analysis"):
    return {"model": "gemini-3.8-flash", "stage": stage, "input_tokens": 100,
            "output_tokens": 30, "thinking_tokens": 10, "usage_available": True}


class AnalysisValidationTests(unittest.TestCase):
    def test_fractional_source_time_round_trip(self):
        for seconds in (0, 0.375, 62.417, 3600.125, 7201.999):
            self.assertAlmostEqual(time_to_seconds(format_time(seconds)), seconds, places=3)
        self.assertEqual(clip_offset(12.375), "12.375s")
        self.assertEqual(time_to_seconds("01:02:03.125"), 3723.125)

    def test_invalid_times_are_rejected_not_replaced_with_zero(self):
        for value in ("garbage", "01:90", "1:99:00", "NaN", "inf", "-1", True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                time_to_seconds(value)

    def test_scene_bounds_use_probe_duration_and_surface_coverage(self):
        result = scenes()
        result.total_duration = "99:59"
        result.scenes[0].start_time = "00:00.500"
        clean = validate_scenes(result, 2)
        self.assertEqual(clean.total_duration, "00:02.000")
        self.assertTrue(any("gap" in w for w in clean.analysis_warnings))
        result.scenes[0].end_time = "00:05"
        with self.assertRaises(ValueError):
            validate_scenes(result, 2)

    def test_out_of_range_and_overlapping_shots_are_rejected(self):
        for values in ([shot(end="00:03")], [shot(end="00:01.500"), shot(start="00:01")], []):
            with self.subTest(values=values), self.assertRaises(ValueError):
                validate_shots(values, 0, 2, 1)

    def test_new_model_output_cannot_self_approve(self):
        item = shot(review_status="approved", timestamp_accuracy="frame_accurate", tags=["cup", "cup", " "])
        item.evidence = [Evidence(description="cup", start_time="00:00.5", end_time="00:01.5")]
        result = validate_shots([item], 0, 2, 7)[0]
        self.assertEqual(result.shot_number, 7)
        self.assertEqual(result.review_status, "unreviewed")
        self.assertEqual(result.timestamp_accuracy, "approximate")
        self.assertEqual(result.tags, ["cup"])

    def test_invalid_evidence_is_dropped_with_warning(self):
        valid, warnings = validate_evidence([
            Evidence(description="Not in clip", start_time="00:09", end_time="00:10"),
        ], 0, 2)
        self.assertEqual(valid, [])
        self.assertTrue(warnings)

    def test_thinking_counts_as_billable_output(self):
        response = SimpleNamespace(usage_metadata=SimpleNamespace(
            prompt_token_count=200, candidates_token_count=30, thoughts_token_count=70,
            total_token_count=300, cached_content_token_count=0,
        ))
        result = extract_usage(response, "gemini-3.8-flash", "summary")
        self.assertEqual(result["output_tokens"], 100)
        self.assertEqual(result["thinking_tokens"], 70)

    def test_unknown_or_missing_usage_is_explicitly_unpriced(self):
        result = estimate_cost(usage_records=[{"model": "future-model", "input_tokens": 20,
                                              "output_tokens": 5}])
        self.assertIsNone(result["estimated_cost_usd"])
        self.assertEqual(result["pricing_status"], "unpriced")
        result = estimate_cost(usage_records=[{"model": "gemini-3.8-flash", "usage_available": False}])
        self.assertIsNone(result["estimated_cost_usd"])

    def test_price_schedule_handles_announced_change(self):
        records = [{"model": "gemini-3.8-flash", "input_tokens": 1_000_000,
                    "output_tokens": 1_000_000}]
        self.assertEqual(estimate_cost(usage_records=records, as_of=date(2026, 9, 9))["estimated_cost_usd"], 4.5)
        self.assertEqual(estimate_cost(usage_records=records, as_of=date(2027, 1, 1))["estimated_cost_usd"], 9)

    def test_truncated_or_empty_response_is_not_success(self):
        with self.assertRaises(ValueError):
            response_text(SimpleNamespace(text='{"shots": []}', candidates=[SimpleNamespace(finish_reason="MAX_TOKENS")]))
        with self.assertRaises(ValueError):
            response_text(SimpleNamespace(text=None, candidates=[]))


class AnalysisProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_clip_offsets_retain_fractional_seconds_and_configured_model(self):
        item = shot(start="00:12.375", end="00:13.625")
        fake = Mock()
        fake.aio.models.generate_content = AsyncMock(return_value=SimpleNamespace(
            text=json.dumps({"shots": [item.model_dump()]}), usage_metadata=None, candidates=[],
        ))
        outline = SceneOutline(scene_number=1, scene_title="Cup", scene_description="Cup",
                               start_time=item.start_time, end_time=item.end_time)
        with patch("app.services.flash_pass.get_client", return_value=fake), \
             patch.object(settings, "gemini_analysis_model", "selected-model"):
            result, _ = await run_shot_detection("uri", "video/mp4", outline, 3, 2)
        request = fake.aio.models.generate_content.call_args.kwargs
        self.assertEqual(request["model"], "selected-model")
        self.assertEqual(request["contents"][0].video_metadata.start_offset, "12.375s")
        self.assertEqual(request["contents"][0].video_metadata.end_offset, "13.625s")
        self.assertEqual(request["config"].http_options.timeout, 90000)
        self.assertEqual(request["config"].http_options.retry_options.attempts, 1)
        self.assertEqual(result[0].shot_number, 3)


class AnalysisPersistenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path_patch = patch.object(settings, "database_path", str(Path(self.directory.name) / "test.sqlite3"))
        self.path_patch.start()
        self.job = create_job(Job(job_id="analysis-test", file_id="provider-file", file_uri="uri",
                                  mime_type="video/mp4", filename="test.mp4", size_bytes=10, local_path="",
                                  technical={"duration_seconds": 2, "has_audio": False},
                                  metadata={"client": "Human choice"},
                                  flash_result={"total_shots": 1, "scenes": [{"shots": [shot().model_dump()]}]},
                                  shot_annotations={"1": {"tags": ["approved-human-tag"]}}))

    def tearDown(self):
        self.path_patch.stop()
        self.directory.cleanup()

    async def test_success_persists_metadata_and_removes_silent_audio_claims(self):
        item = shot(transcript="Invented words", evidence=[Evidence(
            modality="audio", description="Voice", start_time="00:00", end_time="00:02")])
        with patch.object(analyzer, "run_scene_detection", AsyncMock(return_value=(scenes(), usage()))), \
             patch.object(analyzer, "run_shot_detection", AsyncMock(return_value=([item], usage()))):
            events = [event async for event in analyzer.run_analysis(self.job, mode="flash_only")]
        final = get_job(self.job.job_id)
        self.assertEqual(final.status, "complete")
        self.assertEqual(final.transcript, [])
        self.assertEqual(final.flash_result["scenes"][0]["shots"][0]["transcript"], "")
        self.assertEqual(final.metadata["client"], "Human choice")
        self.assertEqual(final.shot_annotations["1"]["tags"], ["approved-human-tag"])
        self.assertTrue(any(event["event"] == "analysis_complete" for event in events))

    async def test_failed_shot_pass_is_error_and_secret_is_not_persisted(self):
        secret = "FAKE_SECRET_MUST_NOT_BE_SAVED"
        with patch.object(analyzer, "run_scene_detection", AsyncMock(return_value=(scenes(), usage()))), \
             patch.object(analyzer, "run_shot_detection", AsyncMock(side_effect=RuntimeError("url?key=" + secret))):
            events = [event async for event in analyzer.run_analysis(self.job, mode="flash_only")]
        final = get_job(self.job.job_id)
        self.assertEqual(final.status, "error")
        self.assertEqual(final.flash_result["total_shots"], 1)  # old successful run preserved
        self.assertEqual(final.shot_annotations["1"]["tags"], ["approved-human-tag"])
        self.assertNotIn(secret, json.dumps(final.warnings) + str(final.error) + json.dumps(events))
        self.assertFalse(any(event["event"] == "analysis_complete" for event in events))

    async def test_changed_interval_archives_review_instead_of_attaching_it_to_new_shot(self):
        with patch.object(analyzer, "run_scene_detection", AsyncMock(return_value=(scenes(), usage()))), \
             patch.object(analyzer, "run_shot_detection", AsyncMock(return_value=([shot(start="00:00.500")], usage()))):
            _ = [event async for event in analyzer.run_analysis(self.job, mode="flash_only")]
        final = get_job(self.job.job_id)
        self.assertEqual(final.status, "complete")
        self.assertEqual(final.shot_annotations, {})
        self.assertEqual(final.analysis_history[-1]["shot_annotations"]["1"]["tags"], ["approved-human-tag"])
        self.assertEqual(final.analysis_history[-1]["flash_result"]["total_shots"], 1)

    async def test_optional_failure_keeps_summary_custom_brief_and_archived_matches(self):
        matches = [{"shot_a": 1, "shot_b": 2, "similarity": .8, "reasons": ["palette"]}]
        self.job = update_job(self.job.job_id, shot_matches=matches)
        custom = {"visual_style": "A blue cup with clean centered framing"}
        with patch.object(analyzer, "run_scene_detection", AsyncMock(return_value=(scenes(), usage()))), \
             patch.object(analyzer, "run_shot_detection", AsyncMock(return_value=([shot()], usage()))), \
             patch.object(analyzer, "run_pro_scene", AsyncMock(side_effect=RuntimeError("private provider text"))), \
             patch.object(analyzer, "run_summary_pass", AsyncMock(return_value=(VideoSummary(executive_summary="Cup"), usage("summary")))), \
             patch.object(analyzer, "run_custom_pass", AsyncMock(return_value=(custom, usage("custom")))) as custom_pass:
            events = [event async for event in analyzer.run_analysis(self.job, custom_prompt="Describe the visual style")]
        final = get_job(self.job.job_id)
        self.assertEqual(final.status, "complete")
        self.assertEqual(final.flash_result["total_shots"], 1)
        self.assertEqual(final.shot_matches, [])
        self.assertEqual(final.analysis_history[-1]["shot_matches"], matches)
        self.assertEqual(final.summary["executive_summary"], "Cup")
        self.assertEqual(final.custom_result, custom)
        custom_pass.assert_awaited_once_with("uri", "video/mp4", "Describe the visual style", api_key=None)
        self.assertTrue(any("Detailed analysis" in warning for warning in final.warnings))
        self.assertFalse(any(event["event"] == "matches_complete" for event in events))
        self.assertEqual([item["stage"] for item in final.cost_estimate["usage_records"]],
                         ["shot_analysis", "summary", "custom"])


if __name__ == "__main__":
    unittest.main()
