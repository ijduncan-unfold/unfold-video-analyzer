"""Portable metadata sidecars; source video and provider credentials stay untouched.

XMP fields follow Adobe's Dynamic Media namespace and Track/FrameCount types:
https://developer.adobe.com/xmp/docs/xmp-namespaces/xmp-dm/
https://developer.adobe.com/xmp/docs/xmp-namespaces/xmp-data-types/track/
Application-specific fields use a separate, explicitly named namespace.
"""
from __future__ import annotations

import csv
import io
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Mapping


SCHEMA_VERSION = "1.0"
SLATE_COLUMNS = (
    ('production_title', 'Production'), ('scene', 'Scene'), ('take', 'Take'),
    ('slate_shot', 'Shot'), ('camera_roll', 'Camera Roll'), ('camera', 'Camera'),
    ('director', 'Director'), ('cinematographer', 'Cinematographer'), ('date', 'Date'),
    ('sound_roll', 'Sound Roll'), ('timecode', 'Timecode'), ('frame_rate', 'Frame Rate'),
    ('raw_text', 'Text'), ('notes', 'Notes'),
)
EXPORT_FORMATS = (
    ("json", "JSON metadata"), ("csv", "CSV shot list"), ("xmp", "XMP sidecar"),
    ("srt", "SRT subtitles"), ("edl", "EDL edit list"), ("fcpxml", "Final Cut Pro XML"),
    ("premiere_xml", "Premiere XML"),
)
NS = {
    "x": "adobe:ns:meta/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "xmp": "http://ns.adobe.com/xap/1.0/",
    "xmpDM": "http://ns.adobe.com/xmp/1.0/DynamicMedia/",
    "va": "urn:video-analyzer:metadata:1.0:",
}
for _prefix, _namespace in NS.items():
    ET.register_namespace(_prefix, _namespace)

_PRIVATE_FIELDS = {
    "camera_companions",
    "local_path", "viewing_path", "viewing_fingerprint", "file_uri", "file_id", "api_key", "apikey", "api_token",
    "access_token", "refresh_token", "authorization", "credentials", "secret",
    "password", "gemini_api_key", "google_api_key",
}


def job_data(job: Any) -> dict:
    if isinstance(job, Mapping):
        return dict(job)
    if is_dataclass(job):
        return asdict(job)
    if hasattr(job, "model_dump"):
        return job.model_dump()
    return dict(vars(job))


def source_filename(value: Any) -> str:
    """Keep the original basename without disclosing a client/server directory."""
    return re.split(r"[/\\]", str(value or "Untitled"))[-1] or "Untitled"


def _public(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _public(item) for key, item in value.items()
                if str(key).casefold() not in _PRIVATE_FIELDS}
    if isinstance(value, (list, tuple)):
        return [_public(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def portable_document(job: Any) -> dict:
    data = job_data(job)
    return _public({
        "schema": "video-analyzer.library",
        "schema_version": SCHEMA_VERSION,
        "export_scope": data.get("export_scope") or {"type": "asset"},
        "asset": {
            "id": data.get("job_id"),
            "filename": source_filename(data.get("filename")),
            "mime_type": data.get("mime_type"),
            "size_bytes": data.get("size_bytes"),
            "created_at": data.get("created_at"),
        },
        "metadata": data.get("metadata") or {},
        "technical": data.get("technical") or {},
        "analysis": {
            "flash_result": data.get("flash_result"),
            "deep_results": data.get("deep_results") or [],
            "summary": data.get("summary"),
            "custom_result": data.get("custom_result"),
            "shot_matches": data.get("shot_matches") or [],
            "rough_cut": data.get("rough_cut"),
        },
        "shot_annotations": data.get("shot_annotations") or {},
        "transcript": data.get("transcript") or [],
        "analysis_history": data.get("analysis_history") or [],
        "provenance": {
            "analysis_config": data.get("analysis_config") or {},
            "analysis_progress": data.get("analysis_progress") or {},
            "status": data.get("status"),
            "warnings": data.get("warnings") or [],
            "cost_estimate": data.get("cost_estimate"),
            "timeline_coordinate_system": "asset-relative seconds",
            "interval_convention": "start-inclusive, end-exclusive",
            "analysis_origin": "AI-generated analysis; reviewed annotations are stored separately",
            "timing_note": "Analysis timestamps are estimates unless separately verified.",
            "technical_origin": "Stored source inspection metadata; missing values remain unknown",
            "export_scope": data.get("export_scope") or {"type": "asset"},
        },
    })


def parse_seconds(value: Any) -> Decimal:
    """Parse seconds or MM:SS / HH:MM:SS with fractions, never frame timecode."""
    if value is None or isinstance(value, bool):
        raise ValueError("A timestamp is missing or invalid")
    text = str(value).strip().replace(",", ".")
    if not re.fullmatch(r"\d+(?::\d{1,2}){0,2}(?:\.\d+)?", text):
        raise ValueError(f"Invalid timestamp: {text!r}; expected seconds, MM:SS or HH:MM:SS")
    parts = text.split(":")
    try:
        values = [Decimal(part) for part in parts]
    except InvalidOperation as exc:
        raise ValueError("Invalid timestamp") from exc
    if len(values) > 1 and values[-1] >= 60:
        raise ValueError("Timestamp seconds must be below 60")
    if len(values) == 3 and values[1] >= 60:
        raise ValueError("Timestamp minutes must be below 60")
    seconds = sum((part * (Decimal(60) ** index)
                   for index, part in enumerate(reversed(values))), Decimal(0))
    if not seconds.is_finite() or seconds < 0:
        raise ValueError("Timestamp must be finite and nonnegative")
    return seconds


def segment_times(segment: dict, duration: Any = None) -> tuple[Decimal, Decimal]:
    start = parse_seconds(segment.get("start_seconds", segment.get("start_time", segment.get("start"))))
    end = parse_seconds(segment.get("end_seconds", segment.get("end_time", segment.get("end"))))
    if end <= start:
        raise ValueError("A segment must end after it starts")
    if duration is not None and end > parse_seconds(duration):
        raise ValueError("A segment extends beyond the source duration")
    return start, end


def iter_shots(data: dict):
    for scene in (data.get("flash_result") or {}).get("scenes", []):
        for shot in scene.get("shots", []):
            yield scene, shot, (data.get("shot_annotations") or {}).get(str(shot.get("shot_number")), {})


def parse_shot_selection(value: str | None) -> list[int] | None:
    """An omitted selection means the asset; an explicit empty one is invalid."""
    if value is None:
        return None
    pieces = value.split(",")
    if not pieces or any(not re.fullmatch(r"[1-9][0-9]{0,9}", part.strip()) for part in pieces):
        raise ValueError("shot_numbers must be a comma-separated list of positive shot numbers")
    return sorted({int(part.strip()) for part in pieces})


def _selected_data(data: dict, shot_numbers: list[int] | None) -> dict:
    """Build an independent selection view without rebasing source coordinates."""
    if shot_numbers is None:
        return data
    if not shot_numbers or any(type(number) is not int or number <= 0 for number in shot_numbers):
        raise ValueError("Select at least one positive shot number")
    selected = set(shot_numbers)
    available = {shot.get("shot_number") for _, shot, _ in iter_shots(data)}
    missing = selected - available
    if missing:
        raise ValueError("Unknown shot numbers: " + ", ".join(str(number) for number in sorted(missing)))
    duration = (data.get("technical") or {}).get("duration_seconds")
    kept_scenes, intervals, seen, warnings = [], [], set(), []
    for scene in (data.get("flash_result") or {}).get("scenes", []):
        shots = []
        for shot in scene.get("shots", []):
            number = shot.get("shot_number")
            if number not in selected:
                continue
            if number in seen:
                raise ValueError("Selected shot numbers are ambiguous in the stored analysis")
            seen.add(number)
            intervals.append(segment_times(shot, duration))
            shots.append(dict(shot))
            warnings.extend(shot.get("analysis_warnings") or [])
        if shots:
            kept_scenes.append({**scene, "shots": shots})

    def overlaps(segment):
        start, end = segment_times(segment, duration)
        return any(start < selected_end and end > selected_start for selected_start, selected_end in intervals)

    transcript = []
    for cue in data.get("transcript") or []:
        try:
            if not isinstance(cue, Mapping):
                raise ValueError("Invalid transcript cue")
            if overlaps(cue):
                transcript.append(dict(cue))
        except ValueError:
            warnings.append("A transcript cue with invalid timing was omitted because its selection overlap is unknown.")

    scene_numbers = {scene.get("scene_number") for scene in kept_scenes}
    scene_numbers.update(number for scene in kept_scenes for shot in scene['shots']
                         for number in shot.get('section_numbers', []))
    owners = {scene.get('scene_number') for scene in kept_scenes}
    kept_scenes.extend({**scene, 'shots': []}
                       for scene in (data.get('flash_result') or {}).get('scenes', [])
                       if scene.get('scene_number') in scene_numbers - owners)
    kept_scenes.sort(key=lambda scene: parse_seconds(scene.get('start_time', '0')))
    deep_results = []
    for deep in data.get("deep_results") or []:
        if deep.get("scene_number") not in scene_numbers:
            continue
        evidence = []
        for observation in deep.get("evidence") or []:
            try:
                if isinstance(observation, Mapping) and overlaps(observation):
                    evidence.append(dict(observation))
            except ValueError:
                continue
        deep_results.append({**deep, "evidence": evidence,
            "context_scope": "Section-wide notes; may describe unselected shots within this included section."})

    flash = {**(data.get("flash_result") or {}), "scenes": kept_scenes,
             "total_shots": len(seen), "total_scenes": len(kept_scenes),
             "analysis_warnings": list(dict.fromkeys(warnings))}
    return {**data, "flash_result": flash,
        "shot_annotations": {key: value for key, value in (data.get("shot_annotations") or {}).items()
                             if str(key) in {str(number) for number in selected}},
        "deep_results": deep_results, "transcript": transcript,
        "summary": None, "custom_result": None, "analysis_history": [],
        "shot_matches": [dict(match) for match in data.get("shot_matches") or []
                         if match.get("shot_a") in selected and match.get("shot_b") in selected],
        "warnings": list(dict.fromkeys(warnings)), "cost_estimate": None,
        "export_scope": {
            "type": "selected_shots", "shot_numbers": sorted(selected),
            "source_timestamps": "Original asset-relative timestamps; no media is rendered or rebased.",
            "asset_context": "Source technical facts and human asset metadata are retained.",
            "deep_analysis": "Only included sections; their untimed notes remain section-wide context.",
            "transcript": "Complete overlapping cues retain original timestamps and text; words are not trimmed.",
            "analysis_progress": "Progress describes the original analysis run, not selection coverage.",
        },
    }


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = "; ".join(str(item) for item in value)
    elif isinstance(value, Mapping):
        value = json.dumps(value, ensure_ascii=False)
    text = str(value)
    # Quoting CSV does not prevent spreadsheet formula execution. Neutralize
    # formula prefixes even when preceded by spaces/control characters.
    if text.startswith(("\t", "\r", "\n")) or text.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def _csv_document(data: dict) -> str:
    out = io.StringIO(newline="")
    out.write("\ufeff")  # Excel detects UTF-8 reliably with a BOM.
    writer = csv.writer(out)
    headers = ["Asset ID", "Filename", "Title", "Client", "Project", "Campaign",
               "Asset Tags", "Rights Status", "Asset Review Status", "Collections",
               "Asset Notes", "Source Duration Seconds", "Source Width", "Source Height", "Has Audio",
               "Scene", "Scene Title", "Shot", "Start", "End", "Start Seconds", "End Seconds",
               "Shot Type", "Camera Movement", "Visual Description", "Audio Notes",
               "Subjects", "Dominant Colors", "Mood", "Reviewed Tags", "Shot Notes",
               "Shot Review Status", "Source Frame Rate", "Source Timecode", "Timing Status",
               "AI Tags", "Actions", "Visible Text", "Potential Logos", "Location", "Transcript", "Evidence"]
    headers += ['Slate ' + title for _, title in SLATE_COLUMNS] + ['Slate Observations']
    writer.writerow(headers)
    meta, technical = data.get("metadata") or {}, data.get("technical") or {}
    common = [data.get("job_id"), source_filename(data.get("filename")), meta.get("title"),
              meta.get("client"), meta.get("project"), meta.get("campaign"), meta.get("tags"),
              meta.get("rights_status"), meta.get("review_status"), meta.get("collections"),
              meta.get("notes"), technical.get("duration_seconds"), technical.get("width"),
              technical.get("height"), technical.get("has_audio")]
    shots = list(iter_shots(data))
    if not shots:
        # An asset can have valuable human metadata before AI analysis exists.
        row = common + [""] * 17 + [technical.get("frame_rate_fraction") or technical.get("frame_rate"),
                                     technical.get("source_timecode"), "No shot analysis available"] + [""] * 7
        writer.writerow([_cell(value) for value in row] + [''] * (len(SLATE_COLUMNS) + 1))
    for scene, shot, reviewed in shots:
        try:
            start, end = segment_times(shot, technical.get("duration_seconds"))
            timing_status = "Estimated; not frame-verified"
        except ValueError as exc:
            start, end, timing_status = "", "", str(exc)
        row = common + [scene.get("scene_number"), scene.get("scene_title"), shot.get("shot_number"),
                        shot.get("start_time", shot.get("start_seconds", shot.get("start"))),
                        shot.get("end_time", shot.get("end_seconds", shot.get("end"))), start, end,
                        shot.get("shot_type"), shot.get("camera_movement"), shot.get("visual_description"),
                        shot.get("audio_notes"), shot.get("subjects"), shot.get("dominant_colors"),
                        shot.get("mood"), reviewed.get("tags"), reviewed.get("notes"),
                        reviewed.get("review_status", "unreviewed"),
                        technical.get("frame_rate_fraction") or technical.get("frame_rate"),
                        technical.get("source_timecode"), timing_status,
                        shot.get("tags"), shot.get("actions"), shot.get("visible_text"), shot.get("logos"),
                        shot.get("location"), shot.get("transcript"),
                        json.dumps(_public(shot.get("evidence") or []), ensure_ascii=False)]
        slates = shot.get('slates') or []
        row += ['; '.join(dict.fromkeys(slate.get(key, '') for slate in slates if slate.get(key)))
                for key, _ in SLATE_COLUMNS]
        row.append(json.dumps(_public(slates), ensure_ascii=False))
        writer.writerow([_cell(value) for value in row])
    return out.getvalue()


def _millis(seconds: Decimal) -> int:
    return int((seconds * 1000).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _srt_time(milliseconds: int) -> str:
    seconds, millis = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"


def _srt_document(data: dict) -> str:
    transcript = data.get("transcript")
    if not transcript:
        raise ValueError("No timed transcript is available. Shot descriptions and audio summaries are not captions.")
    cues = []
    duration = (data.get("technical") or {}).get("duration_seconds")
    for entry in transcript:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("text"), str) or not entry["text"].strip():
            raise ValueError("Every transcript cue must contain actual transcript text")
        start, end = segment_times(entry, duration)
        first, last = _millis(start), _millis(end)
        if last <= first:
            raise ValueError("Transcript cue is shorter than SRT's millisecond precision")
        # Blank lines delimit cues, so preserve text while removing internal blank lines.
        text = "\n".join(line for line in entry["text"].replace("\r\n", "\n").replace("\r", "\n").strip().split("\n") if line.strip())
        cues.append((first, last, text))
    cues.sort(key=lambda cue: (cue[0], cue[1]))
    return "\n\n".join(f"{index}\n{_srt_time(start)} --> {_srt_time(end)}\n{text}"
                       for index, (start, end, text) in enumerate(cues, 1)) + "\n"


def _element(parent, prefix: str, name: str, text: Any = None, **attributes):
    element = ET.SubElement(parent, f"{{{NS[prefix]}}}{name}", attributes)
    if text is not None:
        element.text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(text))
    return element


def _alt(parent, name: str, text: Any):
    prop = _element(parent, "dc", name)
    alt = _element(prop, "rdf", "Alt")
    _element(alt, "rdf", "li", text, **{"{http://www.w3.org/XML/1998/namespace}lang": "x-default"})


def _xmp_document(data: dict) -> str:
    from app.services.editor_metadata import map_fields
    meta, technical = data.get("metadata") or {}, data.get("technical") or {}
    shots = [{**shot, 'human_tags': reviewed.get('tags', [])} for _, shot, reviewed in iter_shots(data)]
    mapping = map_fields(meta, shots, data.get('transcript') or [], data.get('summary'))
    mapped = {field['key']: field['value'] for field in mapping['fields']}
    root = ET.Element(f"{{{NS['x']}}}xmpmeta")
    rdf = _element(root, "rdf", "RDF")
    desc = _element(rdf, "rdf", "Description", **{f"{{{NS['rdf']}}}about": ""})
    _alt(desc, "title", meta.get("title") or source_filename(data.get("filename")))
    _alt(desc, "description", mapped.get('description', ''))
    _element(desc, "dc", "identifier", data.get("job_id", ""))
    _element(desc, "dc", "source", source_filename(data.get("filename")))
    _element(desc, "dc", "format", data.get("mime_type", ""))
    subject = _element(desc, "dc", "subject")
    bag = _element(subject, "rdf", "Bag")
    for tag in mapped.get('keywords', '').split(', '):
        if not tag:
            continue
        _element(bag, "rdf", "li", tag)
    _element(desc, "xmp", "CreatorTool", "Video Analyzer")
    _element(desc, "xmpDM", "client", meta.get("client", ""))
    _element(desc, "xmpDM", "projectName", meta.get("project", ""))
    _element(desc, "xmpDM", "logComment", meta.get("notes", ""))
    if technical.get("frame_rate_fraction") or technical.get("frame_rate"):
        _element(desc, "xmpDM", "videoFrameRate", technical.get("frame_rate_fraction") or technical["frame_rate"])
    # A review flag is not a copyright or license assertion. Keep these fields
    # in our namespace instead of incorrectly mapping to dc:rights/Marked.
    for field in ("campaign", "rights_status", "review_status"):
        _element(desc, "va", field, meta.get(field, ""))
    _element(desc, "va", "schemaVersion", SCHEMA_VERSION)
    _element(desc, "va", "metadata", json.dumps(_public(meta), ensure_ascii=False))
    _element(desc, "va", "technical", json.dumps(_public(technical), ensure_ascii=False))
    _element(desc, "va", "sourceTimecode", technical.get("source_timecode", ""))
    _element(desc, "va", "timingStatus", "Asset-relative estimated analysis markers; not frame-verified")
    _element(desc, "va", "provenance", json.dumps(portable_document(data)["provenance"], ensure_ascii=False))
    # Standard properties are only scalar when the slate readings agree. Keep
    # nonnumeric take labels in our namespace, rather than invalid XMP integers.
    for key, name in (('scene', 'scene'), ('shot', 'shotNumber'), ('camera_roll', 'tapeName'),
                      ('camera', 'cameraLabel'), ('director', 'director'),
                      ('cinematographer', 'directorPhotography'), ('shot_type', 'shotSize'),
                      ('camera_movement', 'cameraMove')):
        if mapped.get(key):
            _element(desc, 'xmpDM', name, mapped[key])
    if mapped.get('take', '').isdigit():
        _element(desc, 'xmpDM', 'takeNumber', int(mapped['take']))
    _element(desc, 'va', 'editorMapping', json.dumps(mapping, ensure_ascii=False))
    _element(desc, 'va', 'analysisRecord', json.dumps(portable_document(data), ensure_ascii=False))
    for field in mapping['fields']:
        if field['xmp'].startswith('va:'):
            _element(desc, 'va', field['xmp'].split(':', 1)[1], field['value'])

    tracks = _element(desc, "xmpDM", "Tracks")
    track_bag = _element(tracks, "rdf", "Bag")
    track = _element(track_bag, "rdf", "li", **{f"{{{NS['rdf']}}}parseType": "Resource"})
    _element(track, "xmpDM", "trackName", "Video Analyzer shot estimates")
    _element(track, "xmpDM", "trackType", "Comment")
    _element(track, "xmpDM", "frameRate", "f1000")  # Milliseconds, not invented source fps.
    markers = _element(track, "xmpDM", "markers")
    sequence = _element(markers, "rdf", "Seq")
    timed_shots = [(segment_times(shot, technical.get("duration_seconds")), scene, shot, reviewed)
                   for scene, shot, reviewed in iter_shots(data)]
    for (start, end), scene, shot, reviewed in sorted(timed_shots, key=lambda row: row[0][0]):
        marker = _element(sequence, "rdf", "li", **{f"{{{NS['rdf']}}}parseType": "Resource"})
        first, last = _millis(start), _millis(end)
        if last <= first:
            raise ValueError("A shot is shorter than XMP marker millisecond precision")
        _element(marker, "xmpDM", "startTime", first)
        _element(marker, "xmpDM", "duration", last - first)
        _element(marker, "xmpDM", "name", f"Scene {scene.get('scene_number', '')} / Shot {shot.get('shot_number', '')}")
        comment = [shot.get("visual_description", ""), f"Shot type: {shot.get('shot_type', '')}",
                   f"Camera: {shot.get('camera_movement', '')}", f"Review: {reviewed.get('review_status', 'unreviewed')}"]
        if reviewed.get("notes"):
            comment.append(f"Reviewed notes: {reviewed['notes']}")
        if reviewed.get("tags"):
            comment.append("Reviewed tags: " + ", ".join(reviewed["tags"]))
        _element(marker, "xmpDM", "comment", "\n".join(comment))
        _element(marker, "va", "shotMetadata", json.dumps(_public(shot), ensure_ascii=False))
    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


def _render_export(data: dict, format: str) -> tuple[bytes | str, str, str]:
    name = format.casefold()
    if name == "json":
        return json.dumps(portable_document(data), ensure_ascii=False, indent=2, allow_nan=False), "application/json", "json"
    if name == "csv":
        return _csv_document(data), "text/csv", "csv"
    if name == "xmp":
        return _xmp_document(data), "application/rdf+xml", "xmp"
    if name == "srt":
        return _srt_document(data), "application/x-subrip", "srt"
    if name in ("edl", "fcpxml"):
        from app.services.nle_export import export_edl, export_fcpxml
        exporter, media_type = (export_edl, "text/plain") if name == "edl" else (export_fcpxml, "application/xml")
        return exporter(data), media_type, name
    if name == "premiere_xml":
        from app.services.premiere_xml import export_premiere_xml
        return export_premiere_xml(data), "application/xml", "xml"
    raise ValueError(f"Unsupported export format: {format}")


def export_library(job: Any, format: str, shot_numbers: list[int] | None = None) -> tuple[bytes | str, str, str]:
    """Return content, MIME type, extension for the asset or selected source shots."""
    return _render_export(_selected_data(job_data(job), shot_numbers), format)


def export_options(job: Any, shot_numbers: list[int] | None = None) -> dict:
    """Use the download renderers themselves so format availability cannot drift."""
    data = _selected_data(job_data(job), shot_numbers)
    formats = []
    for name, label in EXPORT_FORMATS:
        try:
            _render_export(data, name)
        except ValueError as error:
            formats.append({"format": name, "label": label, "available": False, "reason": str(error)})
        else:
            formats.append({"format": name, "label": label, "available": True, "reason": None})
    return {"scope": "asset" if shot_numbers is None else "selected_shots",
            "shot_numbers": sorted({shot.get("shot_number") for _, shot, _ in iter_shots(data)}),
            "formats": formats}
