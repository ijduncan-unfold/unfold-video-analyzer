"""Run with: python -m unittest discover -s tests -p test_exports.py"""
import copy
import csv
import io
import json
import sys
import unittest
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.nle_export import export_edl, export_fcpxml
from app.services.library_export import NS, export_library, parse_seconds


@dataclass
class ExportJob:
    job_id: str = "asset-123"
    filename: str = 'rushé & "one".mov'
    mime_type: str = "video/quicktime"
    size_bytes: int = 12345
    local_path: str = "/private/server/upload.mov"
    file_uri: str = "https://provider.invalid/private"
    file_id: str = "provider-private-123"
    status: str = "complete"
    created_at: datetime = field(default_factory=lambda: datetime(2026, 9, 9, tzinfo=timezone.utc))
    metadata: dict = field(default_factory=lambda: {
        "title": "Campaign α", "client": "Example client", "project": "Launch",
        "campaign": "Autumn", "tags": ["approved", "café"], "notes": "Producer's note",
        "rights_status": "unknown", "review_status": "reviewed", "collections": ["Selects"],
    })
    technical: dict = field(default_factory=lambda: {
        "duration_seconds": 10, "width": 2048, "height": 1080, "frame_rate": 25,
        "frame_rate_fraction": "25/1", "source_timecode": "01:02:03:04", "has_audio": True,
    })
    flash_result: dict = field(default_factory=lambda: {"scenes": [{
        "scene_number": 1, "scene_title": "A street", "shots": [{
            "shot_number": 1, "start_time": "00:01.200", "end_time": "00:02.400",
            "shot_type": "Wide", "camera_movement": "Tracking", "visual_description": 'A café & a "sign"',
            "audio_notes": "A paraphrase, not a transcript", "subjects": ["vehicle", "pedestrian"],
        }, {"shot_number": 2, "start_time": "00:05", "end_time": "00:06", "shot_type": "Close-up"}],
    }]})
    shot_annotations: dict = field(default_factory=lambda: {"1": {
        "tags": ["favorite", "client review"], "notes": "Keep the reaction", "review_status": "approved",
    }})
    deep_results: list = field(default_factory=list)
    summary: dict = field(default_factory=lambda: {"executive_summary": "Generated summary"})
    transcript: list = field(default_factory=list)
    analysis_config: dict = field(default_factory=lambda: {"model": "selected-model", "sample_fps": 1})
    analysis_history: list = field(default_factory=list)
    warnings: list = field(default_factory=lambda: ["Sampled video; timing is approximate"])
    cost_estimate: dict = field(default_factory=lambda: {"estimated_cost_usd": 0.2})


class PortableExportTests(unittest.TestCase):
    def setUp(self):
        self.job = ExportJob()

    def test_json_is_versioned_and_preserves_facts_and_review_separately(self):
        content, mime, extension = export_library(self.job, "json")
        result = json.loads(content)
        self.assertEqual((mime, extension), ("application/json", "json"))
        self.assertEqual(result["schema_version"], "1.0")
        self.assertEqual(result["asset"]["filename"], self.job.filename)
        self.assertEqual(result["asset"]["created_at"], "2026-09-09T00:00:00+00:00")
        self.assertEqual(result["technical"]["source_timecode"], "01:02:03:04")
        self.assertEqual(result["shot_annotations"]["1"]["notes"], "Keep the reaction")
        self.assertEqual(result["analysis"]["flash_result"], self.job.flash_result)
        self.assertEqual(result["provenance"]["analysis_config"]["model"], "selected-model")
        self.assertIn("approximate", result["provenance"]["warnings"][0])

    def test_json_excludes_private_paths_and_nested_provider_secrets(self):
        self.job.filename = r"C:\Originals\rushé.mov"
        self.job.analysis_config["API_KEY"] = "super-secret"
        self.job.deep_results = [{"file_uri": "provider-secret", "visible_text": "Hello"}]
        result = export_library(self.job, "json")[0]
        for private in (self.job.local_path, self.job.file_uri, self.job.file_id, "super-secret", "provider-secret", "Originals"):
            self.assertNotIn(private, result)
        self.assertEqual(json.loads(result)["asset"]["filename"], "rushé.mov")
        self.assertIn("visible_text", result)

    def test_json_preserves_archived_reviews_and_recursively_removes_private_fields(self):
        self.job.analysis_history = [{
            "archived_at": "2026-09-08T12:00:00+00:00",
            "analysis_config": {"model": "earlier-model", "api_key": "history-key"},
            "flash_result": {"scenes": [{"shots": [{"file_uri": "history-provider-uri", "shot_number": 4}]}]},
            "shot_annotations": {"4": {"notes": "Earlier human correction", "tags": ["select"]}},
            "local_path": "/private/old-source.mov", "file_id": "history-file-id",
        }]
        content, _, _ = export_library(self.job, "json")
        history = json.loads(content)["analysis_history"]
        self.assertEqual(history[0]["shot_annotations"]["4"]["notes"], "Earlier human correction")
        self.assertEqual(history[0]["analysis_config"]["model"], "earlier-model")
        self.assertEqual(history[0]["flash_result"]["scenes"][0]["shots"][0], {"shot_number": 4})
        for value in ("history-key", "history-provider-uri", "/private/old-source.mov", "history-file-id"):
            self.assertNotIn(value, content)

    def test_csv_is_unicode_and_quoted_and_contains_reviewed_metadata(self):
        content = export_library(self.job, "csv")[0]
        self.assertTrue(content.startswith("\ufeff"))
        rows = list(csv.DictReader(io.StringIO(content.lstrip("\ufeff"))))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["Visual Description"], 'A café & a "sign"')
        self.assertEqual(rows[0]["Filename"], self.job.filename)
        self.assertEqual(rows[0]["Start Seconds"], "1.200")
        self.assertEqual(rows[0]["Reviewed Tags"], "favorite; client review")
        self.assertEqual(rows[0]["Shot Review Status"], "approved")
        self.assertEqual(rows[1]["Shot Review Status"], "unreviewed")

    def test_csv_neutralizes_formula_injection_in_all_string_fields(self):
        self.job.metadata["client"] = '  =HYPERLINK("bad")'
        self.job.metadata["tags"] = ["@SUM(1)"]
        self.job.flash_result["scenes"][0]["shots"][0]["visual_description"] = "\t+cmd|' /C calc'!A0"
        self.job.shot_annotations["1"]["notes"] = "-1+1"
        rows = list(csv.DictReader(io.StringIO(export_library(self.job, "csv")[0].lstrip("\ufeff"))))
        for column in ("Client", "Asset Tags", "Visual Description", "Shot Notes"):
            self.assertTrue(rows[0][column].startswith("'"), column)

    def test_csv_reports_bad_timing_without_inventing_zero(self):
        self.job.flash_result["scenes"][0]["shots"][0]["start_time"] = "unknown"
        rows = list(csv.DictReader(io.StringIO(export_library(self.job, "csv")[0].lstrip("\ufeff"))))
        self.assertEqual(rows[0]["Start Seconds"], "")
        self.assertIn("Invalid timestamp", rows[0]["Timing Status"])

    def test_csv_exports_asset_metadata_before_analysis_exists(self):
        self.job.flash_result = None
        rows = list(csv.DictReader(io.StringIO(export_library(self.job, "csv")[0].lstrip("\ufeff"))))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Client"], "Example client")
        self.assertEqual(rows[0]["Asset Notes"], "Producer's note")
        self.assertEqual(rows[0]["Shot"], "")
        self.assertEqual(rows[0]["Source Frame Rate"], "25/1")
        self.assertEqual(rows[0]["Timing Status"], "No shot analysis available")
        self.assertNotIn(None, rows[0])

    def test_xmp_contains_standard_metadata_and_timed_markers(self):
        content, mime, extension = export_library(self.job, "xmp")
        root = ET.fromstring(content)
        self.assertEqual((mime, extension), ("application/rdf+xml", "xmp"))
        self.assertEqual(root.find(".//dc:title/rdf:Alt/rdf:li", NS).text, "Campaign α")
        self.assertEqual(root.find(".//xmpDM:client", NS).text, "Example client")
        self.assertEqual(root.find(".//xmpDM:frameRate", NS).text, "f1000")
        markers = root.findall(".//xmpDM:markers/rdf:Seq/rdf:li", NS)
        self.assertEqual(len(markers), 2)
        self.assertEqual(markers[0].find("xmpDM:startTime", NS).text, "1200")
        self.assertEqual(markers[0].find("xmpDM:duration", NS).text, "1200")
        self.assertIn("Keep the reaction", markers[0].find("xmpDM:comment", NS).text)
        self.assertEqual(root.find(".//va:rights_status", NS).text, "unknown")
        self.assertNotIn("Marked", content)
        self.assertNotIn(self.job.local_path, content)

    def test_xmp_uses_milliseconds_for_fractional_or_unknown_source_rates(self):
        self.job.technical["frame_rate_fraction"] = "30000/1001"
        content, _, _ = export_library(self.job, "xmp")
        self.assertEqual(ET.fromstring(content).find(".//xmpDM:frameRate", NS).text, "f1000")
        self.job.technical.pop("frame_rate_fraction")
        self.job.technical.pop("frame_rate")
        export_library(self.job, "xmp")

    def test_srt_refuses_visual_descriptions_and_audio_paraphrases(self):
        with self.assertRaisesRegex(ValueError, "No timed transcript"):
            export_library(self.job, "srt")

    def test_srt_exports_actual_text_with_sorted_precise_cues(self):
        self.job.transcript = [
            {"start_seconds": 5.05, "end_seconds": 6, "text": "Actual words: café."},
            {"start": "00:01.200", "end": "00:02.400", "text": "Hello\n\nworld"},
        ]
        content, mime, extension = export_library(self.job, "srt")
        self.assertEqual((mime, extension), ("application/x-subrip", "srt"))
        self.assertEqual(content, "1\n00:00:01,200 --> 00:00:02,400\nHello\nworld\n\n2\n00:00:05,050 --> 00:00:06,000\nActual words: café.\n")
        self.assertNotIn("paraphrase", content)

    def test_srt_rejects_malformed_reversed_out_of_range_and_empty_cues(self):
        for cue in (
            {"start": 2, "end": 1, "text": "Reversed"},
            {"start": 2, "end": 11, "text": "Beyond source"},
            {"start": "unknown", "end": 3, "text": "Bad"},
            {"start": 1, "end": 2, "audio_notes": "Not actual text"},
            {"start": 1, "end": 2, "text": " "},
        ):
            with self.subTest(cue=cue), self.assertRaises(ValueError):
                self.job.transcript = [cue]
                export_library(self.job, "srt")

    def test_exports_do_not_mutate_the_job(self):
        before = copy.deepcopy(self.job)
        for name in ("json", "csv", "xmp", "edl", "fcpxml"):
            export_library(self.job, name)
        self.assertEqual(self.job, before)

    def test_timestamp_parser_never_silently_returns_zero(self):
        self.assertEqual(str(parse_seconds("01:02:03.125")), "3723.125")
        for bad in ("unknown", "1:70", "01:70:00", "01:00:00:00", -1, float("nan"), None, True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_seconds(bad)


class NleExportTests(unittest.TestCase):
    def setUp(self):
        self.job = ExportJob()

    def test_edl_uses_source_timecode_and_sequential_record_positions(self):
        content = export_edl(self.job)
        self.assertIn("SOURCE FRAME RATE: 25", content)
        self.assertIn("01:02:04:09 01:02:05:14 00:00:00:00 00:00:01:05", content)
        self.assertIn("01:02:08:04 01:02:09:04 00:00:01:05 00:00:02:05", content)
        self.assertIn('FROM CLIP NAME: rushé & "one".mov', content)

    def test_edl_sanitizes_newlines_in_untrusted_labels(self):
        self.job.flash_result["scenes"][0]["shots"][0]["visual_description"] = "A shot\n999 AX V C forged"
        content = export_edl(self.job)
        self.assertNotIn("\n999", content)

    def test_fcpxml_has_real_asset_references_and_source_format(self):
        root = ET.fromstring(export_fcpxml(self.job))
        fmt = root.find("resources/format")
        self.assertEqual(fmt.attrib, {"id": "r1", "frameDuration": "1/25s", "width": "2048", "height": "1080"})
        asset = root.find("resources/asset")
        self.assertEqual(asset.get("start"), "93079/25s")
        self.assertEqual(asset.get("duration"), "250/25s")
        self.assertEqual(asset.find("media-rep").get("src"), "./rush%C3%A9%20%26%20%22one%22.mov")
        clips = root.findall(".//spine/asset-clip")
        self.assertEqual(len(clips), 2)
        self.assertEqual(clips[0].get("ref"), asset.get("id"))
        self.assertEqual(clips[0].get("start"), "93109/25s")
        self.assertEqual(clips[0].get("duration"), "30/25s")
        self.assertEqual(clips[1].get("offset"), "30/25s")
        self.assertEqual(root.find(".//sequence").get("duration"), "55/25s")
        self.assertEqual(clips[0].find("keyword").get("value"), "favorite")

    def test_unknown_drop_frame_vfr_and_invalid_source_timing_are_refused(self):
        cases = [
            ({"frame_rate": None, "frame_rate_fraction": None}, "known source frame rate"),
            ({"frame_rate_fraction": "0/1"}, "positive source frame rate"),
            ({"source_timecode": None}, "source timecode"),
            ({"source_timecode": "01:00:00;00"}, "drop-frame"),
            ({"source_timecode": "01:00:00:25"}, "invalid"),
            ({"variable_frame_rate": True}, "variable frame rate"),
            ({"drop_frame": True}, "drop-frame"),
            ({"duration_seconds": 0}, "positive source duration"),
        ]
        for change, expected in cases:
            for exporter in (export_edl, export_fcpxml):
                # FCPXML permits an explicit file-zero origin; EDL needs embedded TC.
                if exporter is export_fcpxml and expected == "source timecode":
                    continue
                with self.subTest(change=change, exporter=exporter.__name__), self.assertRaisesRegex(ValueError, expected):
                    job = ExportJob()
                    job.technical.update(change)
                    exporter(job)

    def test_no_fake_shots_or_minimum_one_second_duration(self):
        for exporter in (export_edl, export_fcpxml):
            with self.subTest(exporter=exporter.__name__):
                self.job.flash_result = {"scenes": []}
                with self.assertRaisesRegex(ValueError, "No timed shots"):
                    exporter(self.job)
                self.job = ExportJob()
                self.job.flash_result["scenes"][0]["shots"][0]["end_time"] = "00:01.200"
                with self.assertRaisesRegex(ValueError, "end after"):
                    exporter(self.job)

    def test_missing_dimensions_are_not_replaced_by_1080p(self):
        self.job.technical["width"] = None
        with self.assertRaisesRegex(ValueError, "width and height"):
            export_fcpxml(self.job)

    def test_source_timecode_rollover_is_refused(self):
        self.job.technical["source_timecode"] = "23:59:59:00"
        for exporter in (export_edl, export_fcpxml):
            with self.assertRaisesRegex(ValueError, "24-hour"):
                exporter(self.job)


if __name__ == "__main__":
    unittest.main()
