"""Selected exports and availability use isolated fixtures and never invoke a provider."""
import copy
import csv
import io
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict
from fractions import Fraction

import pytest

from app.services.job_store import get_job, update_job
from app.services.library_export import NS, export_library, export_options, parse_shot_selection


@pytest.fixture
def export_asset(asset):
    flash = copy.deepcopy(asset.flash_result)
    flash.update(total_shots=4, total_scenes=3, total_duration="00:20.000",
                 analysis_warnings=["UNRELATED_FLASH_WARNING"])
    flash["scenes"][0]["shots"][0]["visual_description"] = "UNRELATED_SHOT"
    for number, start, end in ((3, "00:10.000", "00:12.000"), (4, "00:15.000", "00:18.000")):
        flash["scenes"].append({"scene_number": number - 1, "scene_title": f"Section {number - 1}",
            "start_time": start, "end_time": end, "shots": [{
                "shot_number": number, "start_time": start, "end_time": end,
                "shot_type": "CU", "camera_movement": "static", "visual_description": f"Shot {number}",
                "tags": ["circle"], "audio_notes": "", "subjects": ["object"],
            }]})
    return update_job(asset.job_id, flash_result=flash, technical={**asset.technical, "duration_seconds": 20},
        analysis_config={"started_at": "2026-09-10T10:00:00+00:00", "mode": "flash_pro"},
        analysis_history=[{"summary": "UNRELATED_HISTORY"}], summary={"executive_summary": "UNRELATED_SUMMARY"},
        custom_result={"text": "UNRELATED_CUSTOM"}, warnings=["UNRELATED_WARNING"],
        shot_annotations={"1": {"notes": "UNRELATED_REVIEW"}, "2": {"tags": ["select"]}, "3": {"notes": "Keep"}},
        shot_matches=[{"shot_a": 2, "shot_b": 3, "reasons": ["Similar shape"]},
                      {"shot_a": 1, "shot_b": 2, "reasons": ["UNRELATED_MATCH"]}],
        deep_results=[{"scene_number": 1, "visual_analysis": {"composition": "Section context"}, "evidence": [
            {"description": "UNRELATED_EVIDENCE", "start_time": "00:01", "end_time": "00:02"}]},
            {"scene_number": 2, "visual_analysis": {"composition": "Circle"}, "evidence": [
                {"description": "Object", "start_time": "00:10.100", "end_time": "00:11"}]},
            {"scene_number": 3, "visual_analysis": {"composition": "UNRELATED_DEEP"}}],
        transcript=[{"start_time": "00:01", "end_time": "00:02", "text": "UNRELATED_WORDS"},
            {"start_time": "00:03", "end_time": "00:04", "text": "A cue across the shot boundary."},
            {"start_time": "00:10.100", "end_time": "00:11", "text": "Circle words."},
            {"start_time": "00:12", "end_time": "00:13", "text": "UNRELATED_BOUNDARY_CUE"}])


def test_selected_json_filters_context_preserves_source_times_and_does_not_mutate(export_asset):
    before = asdict(export_asset)
    text, _, _ = export_library(export_asset, "json", [3, 2, 2])
    data = json.loads(text)
    assert data["export_scope"]["type"] == "selected_shots"
    assert data["export_scope"]["shot_numbers"] == [2, 3]
    flash = data["analysis"]["flash_result"]
    assert flash["total_shots"] == 2 and flash["total_scenes"] == 2
    assert [s["shot_number"] for c in flash["scenes"] for s in c["shots"]] == [2, 3]
    assert flash["scenes"][0]["shots"][0]["start_time"] == "00:03.500"
    assert data["technical"]["duration_seconds"] == 20
    assert data["technical"]["source_timecode"] == "01:00:00:00"
    assert set(data["shot_annotations"]) == {"2", "3"}
    assert len(data["analysis"]["deep_results"]) == 2
    assert "Section-wide" in data["analysis"]["deep_results"][0]["context_scope"]
    assert len(data["analysis"]["shot_matches"]) == 1
    assert data["analysis_history"] == [] and data["analysis"]["summary"] is None
    assert data["transcript"][0]["start_time"] == "00:03"  # do not fabricate word-level trimming
    assert "UNRELATED_" not in text
    assert "private-provider" not in text
    assert asdict(export_asset) == before


def test_selection_reaches_all_six_exporters_and_keeps_source_coordinates(export_asset):
    rows = list(csv.DictReader(io.StringIO(export_library(export_asset, "csv", [3])[0].lstrip("\ufeff"))))
    assert len(rows) == 1 and rows[0]["Shot"] == "3" and rows[0]["Start Seconds"] == "10.000"
    assert rows[0]["AI Tags"] == "circle"
    xmp = ET.fromstring(export_library(export_asset, "xmp", [3])[0])
    markers = xmp.findall(".//xmpDM:markers/rdf:Seq/rdf:li", NS)
    assert len(markers) == 1 and markers[0].find("xmpDM:startTime", NS).text == "10000"
    assert json.loads(markers[0].find("va:shotMetadata", NS).text)["tags"] == ["circle"]
    srt = export_library(export_asset, "srt", [3])[0]
    assert "00:00:10,100 --> 00:00:11,000" in srt and "Circle words" in srt
    assert "across" not in srt and "UNRELATED" not in srt
    edl = export_library(export_asset, "edl", [3])[0]
    assert len(re.findall(r"^\d{3}\s+AX", edl, re.MULTILINE)) == 1
    assert "01:00:10:00 01:00:12:00 00:00:00:00 00:00:02:00" in edl
    xml = ET.fromstring(export_library(export_asset, "fcpxml", [3])[0])
    clips = xml.findall(".//spine/asset-clip")
    assert len(clips) == 1 and clips[0].get("name") == "S2_Shot3"
    assert clips[0].get("start") == "90250/25s"
    assert clips[0].get("offset") == "0/25s" and clips[0].get("duration") == "50/25s"
    assert xml.find("resources/asset").get("duration") == "500/25s"
    assert json.loads(export_library(export_asset, "json")[0])["analysis"]["flash_result"]["total_shots"] == 4


@pytest.mark.parametrize("selection", ["", " ", "0", "-1", "1.5", "1,", ",1", "1,,2", "1;2", "x", "01", "999"])
def test_malformed_and_unknown_selections_are_rejected_by_both_routes(client, export_asset, selection):
    for endpoint in ("export", "export-options"):
        response = client.get(f"/api/library/{export_asset.job_id}/{endpoint}", params={"shot_numbers": selection})
        assert response.status_code == 422


def test_options_match_real_download_validation_for_selected_scope(client, export_asset):
    for changes, selection in (({}, "3"), ({"source_timecode": None}, "3"),
                               ({"frame_rate_fraction": "30000/1001", "source_timecode": None}, "3"),
                               ({"width": None}, "3"), ({}, "4")):
        update_job(export_asset.job_id, technical={**export_asset.technical, **changes})
        options = client.get(f"/api/library/{export_asset.job_id}/export-options", params={"shot_numbers": selection})
        assert options.status_code == 200
        result = options.json()
        assert result["scope"] == "selected_shots" and result["shot_numbers"] == [int(selection)]
        assert len(result["formats"]) == 7
        for option in result["formats"]:
            response = client.get(f"/api/library/{export_asset.job_id}/export", params={
                "format": option["format"], "shot_numbers": selection})
            assert option["available"] is (response.status_code == 200)
            if not option["available"]:
                assert option["reason"] == response.json()["detail"]
            else:
                assert option["reason"] is None


def test_stale_analysis_version_is_rejected_before_export_or_availability(client, export_asset):
    for endpoint in ("export", "export-options"):
        url = f"/api/library/{export_asset.job_id}/{endpoint}"
        assert client.get(url, params={"shot_numbers": "2", "analysis_started_at": "old-run"}).status_code == 409
        assert client.get(url, params={"shot_numbers": "2",
            "analysis_started_at": export_asset.analysis_config["started_at"]}).status_code == 200
    response = client.get(f"/api/library/{export_asset.job_id}/export", params={"shot_numbers": "3, 2,3"})
    assert response.json()["export_scope"]["shot_numbers"] == [2, 3]
    assert "selected_shots" in response.headers["content-disposition"]


@pytest.mark.parametrize("rate", ["24000/1001", "30000/1001", "60000/1001"])
def test_fcpxml_single_shot_uses_exact_rational_file_zero_when_no_embedded_timecode(export_asset, rate):
    job = copy.deepcopy(export_asset)
    job.technical.update(frame_rate_fraction=rate, source_timecode=None)
    xml_text = export_library(job, "fcpxml", [3])[0]
    xml = ET.fromstring(xml_text)
    fps = Fraction(rate)
    frame = Fraction(1, 1) / fps
    fmt = xml.find("resources/format")
    assert Fraction(fmt.get("frameDuration").removesuffix("s")) == frame
    asset = xml.find("resources/asset")
    assert Fraction(asset.get("start").removesuffix("s")) == 0
    assert asset.find("metadata/md").get("value") == "source_relative_file_zero"
    clip = xml.find(".//spine/asset-clip")
    assert Fraction(clip.get("start").removesuffix("s")) / frame == round(Fraction(10) * fps)
    assert Fraction(clip.get("duration").removesuffix("s")) / frame == round(Fraction(12) * fps) - round(Fraction(10) * fps)
    assert "not an asserted original timecode" in xml_text
    assert job.technical["source_timecode"] is None
    with pytest.raises(ValueError, match="source timecode"):
        export_library(job, "edl", [3])


def test_fcpxml_fractional_ndf_embedded_timecode_counts_nominal_frames(export_asset):
    job = copy.deepcopy(export_asset)
    job.technical.update(frame_rate_fraction="30000/1001", source_timecode="01:00:00:00")
    xml = ET.fromstring(export_library(job, "fcpxml", [3])[0])
    assert xml.find("resources/asset").get("start") == "108108000/30000s"
    assert xml.find("resources/asset/metadata/md").get("value") == "embedded_source_timecode"


def test_fcpxml_prefers_verified_nominal_cadence_over_rounded_container_average(export_asset):
    job = copy.deepcopy(export_asset)
    job.technical.update(frame_rate=23.97602468920166, frame_rate_fraction='100755000/4202323',
                         nominal_frame_rate_fraction='24000/1001', nominal_frame_rate_verified=True,
                         frame_rate_verification='ffprobe_full_packet_pts', source_timecode=None)
    xml = ET.fromstring(export_library(job, 'fcpxml', [3])[0])
    assert xml.find('resources/format').get('frameDuration') == '1001/24000s'
    clip = xml.find('.//spine/asset-clip')
    assert Fraction(clip.get('start').removesuffix('s')) == Fraction(240 * 1001, 24000)
    assert clip.get('offset') == '0/24000s'
    assert job.technical['frame_rate_fraction'] == '100755000/4202323'
    for changes in ({'nominal_frame_rate_verified': False}, {'frame_rate_verification': 'guessed'},
                    {'variable_frame_rate': True}):
        unsupported = copy.deepcopy(job)
        unsupported.technical.update(changes)
        with pytest.raises(ValueError, match='verified constant|variable frame rate'):
            export_library(unsupported, 'fcpxml', [3])


def test_unverified_cadence_cannot_export_edl_even_with_integer_average_and_timecode(export_asset):
    job = copy.deepcopy(export_asset)
    job.technical.update(nominal_frame_rate_fraction='25/1', nominal_frame_rate_verified=False,
                         frame_rate_verification='unverified')
    with pytest.raises(ValueError, match='verified constant'):
        export_library(job, 'edl', [3])


def test_selection_without_transcript_disables_srt_and_keeps_non_timing_exports(export_asset):
    options = {item["format"]: item for item in export_options(export_asset, [4])["formats"]}
    assert options["srt"]["available"] is False and "No timed transcript" in options["srt"]["reason"]
    assert all(options[name]["available"] for name in ("json", "csv", "xmp", "edl", "fcpxml"))
    assert parse_shot_selection(None) is None
    assert parse_shot_selection("3, 2,3") == [2, 3]
