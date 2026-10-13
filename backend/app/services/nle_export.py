"""Source-timed EDL and FCPXML exporters used by the library workspace."""
import re
import json
import xml.etree.ElementTree as ET
from decimal import Decimal
from fractions import Fraction
from urllib.parse import quote

from app.services.library_export import iter_shots, job_data, parse_seconds, segment_times, source_filename
from app.services.source_timing import verified_rate


def _source_timing(data: dict) -> tuple[Fraction, int, Decimal]:
    try:
        rate, source_start, duration, basis = _fcpxml_timing(data)
    except ValueError as exc:
        raise ValueError(str(exc).replace("FCPXML", "EDL")) from exc
    if basis != 'embedded_source_timecode':
        raise ValueError('EDL export requires a known embedded source timecode in HH:MM:SS:FF format')
    return rate, source_start, duration


def _frames(seconds: Decimal, fps: int | Fraction) -> int:
    scaled = Fraction(seconds) * fps
    return (2 * scaled.numerator + scaled.denominator) // (2 * scaled.denominator)


def _fcpxml_timing(data: dict) -> tuple[Fraction, int, Decimal, str]:
    """FCPXML uses rational source-frame durations, independently of CMX timecode.

    Apple documents 1001/30000s frames and the meanings of start/offset:
    https://developer.apple.com/documentation/professional-video-applications/timing-attributes
    File-relative zero is a declared local timeline, not inferred embedded TC:
    https://developer.apple.com/documentation/professional-video-applications/creating-fcpxml-documents
    """
    technical = data.get("technical") or {}
    if technical.get("variable_frame_rate") or technical.get("is_vfr"):
        raise ValueError("FCPXML export does not support variable frame rate media; use JSON, CSV or XMP")
    timecode = str(technical.get("source_timecode") or "")
    if (technical.get("drop_frame") or ";" in timecode or
        str(technical.get("timecode_mode", "")).upper() in {"DF", "DROP", "DROP-FRAME"}):
        raise ValueError("FCPXML export does not yet support drop-frame source timecode; use JSON, CSV or XMP")
    verified_nominal = verified_rate(technical)
    if (any(key in technical for key in ("nominal_frame_rate_verified", "nominal_frame_rate_fraction"))
            and not verified_nominal):
        raise ValueError("FCPXML requires verified constant frame timing; source cadence is unknown or irregular")
    raw_rate = verified_nominal or technical.get("frame_rate_fraction") or technical.get("frame_rate")
    try:
        rate = Fraction(str(raw_rate))
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError("FCPXML export requires a known source frame rate") from exc
    if rate <= 0:
        raise ValueError("FCPXML export requires a known positive source frame rate")
    if rate.denominator != 1 and not (verified_nominal or technical.get("frame_rate_fraction")):
        raise ValueError("Fractional FCPXML timing requires an exact source frame-rate fraction")
    supported = {Fraction(value) for value in (24, 25, 30, 48, 50, 60)} | {
        Fraction(value, 1001) for value in (24000, 30000, 48000, 60000)}
    if rate not in supported:
        raise ValueError("Unsupported source frame rate for FCPXML export")
    nominal_fps = (rate.numerator + rate.denominator - 1) // rate.denominator
    source_start = 0
    if timecode:
        match = re.fullmatch(r"(\d{2}):(\d{2}):(\d{2}):(\d{2})", timecode)
        if not match:
            raise ValueError("Embedded source timecode must use HH:MM:SS:FF format")
        hours, minutes, seconds, frames = map(int, match.groups())
        if hours >= 24 or minutes >= 60 or seconds >= 60 or frames >= nominal_fps:
            raise ValueError("Source timecode is invalid for the source frame rate")
        source_start = ((hours * 60 + minutes) * 60 + seconds) * nominal_fps + frames
    duration = parse_seconds(technical.get("duration_seconds"))
    if duration <= 0:
        raise ValueError("FCPXML export requires a known positive source duration")
    if timecode:
        _frame_timecode(source_start + _frames(duration, rate), nominal_fps)
    return rate, source_start, duration, "embedded_source_timecode" if timecode else "source_relative_file_zero"


def _frame_timecode(frame: int, fps: int) -> str:
    seconds, frames = divmod(frame, fps)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours >= 24:
        raise ValueError("NLE export would cross the 24-hour timecode boundary")
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frames:02d}"


def _nle_shots(data: dict, fps: int | Fraction, duration: Decimal, preserve_order=False):
    rows = []
    for scene, shot, reviewed in iter_shots(data):
        start, end = segment_times(shot, duration)
        first, last = _frames(start, fps), _frames(end, fps)
        if last <= first:
            raise ValueError("A shot is shorter than one source frame after rounding")
        rows.append((first, last, scene, shot, reviewed))
    if not rows:
        raise ValueError("No timed shots are available to export")
    return rows if preserve_order else sorted(rows, key=lambda item: item[0])


def _one_line(value) -> str:
    return " ".join(str(value or "").split())


def export_edl(job) -> str:
    """CMX3600 video-only selects, with verified non-drop source timing."""
    data = job_data(job)
    fps, source_start, duration = _source_timing(data)
    if fps not in {Fraction(24), Fraction(25), Fraction(30), Fraction(24000, 1001), Fraction(30000, 1001)}:
        raise ValueError("CMX3600 export supports 24, 25, 30, 23.976 or 29.97 fps non-drop sources")
    timebase = (fps.numerator + fps.denominator - 1) // fps.denominator
    shots = _nle_shots(data, fps, duration)
    if len(shots) > 999:
        raise ValueError("CMX3600 export is limited to 999 events")
    filename = _one_line(source_filename(data.get("filename")))
    lines = [f"TITLE: {filename}", "FCM: NON-DROP FRAME", f"* SOURCE FRAME RATE: {fps}",
             "* VIDEO ONLY; SOURCE MEDIA IS NOT INCLUDED",
             "* ESTIMATED ANALYSIS BOUNDARIES ROUNDED TO THE NEAREST SOURCE FRAME", ""]
    record = 0
    for index, (first, last, scene, shot, reviewed) in enumerate(shots, 1):
        count = last - first
        lines.append(f"{index:03d}  AX       V     C        "
                     f"{_frame_timecode(source_start + first, timebase)} {_frame_timecode(source_start + last, timebase)} "
                     f"{_frame_timecode(record, timebase)} {_frame_timecode(record + count, timebase)}")
        lines.extend([f"* FROM CLIP NAME: {filename}",
                      f"* COMMENT: {_one_line(shot.get('visual_description'))}",
                      f"* SHOT TYPE: {_one_line(shot.get('shot_type'))}",
                      f"* CAMERA: {_one_line(shot.get('camera_movement'))}"])
        if reviewed.get("notes"):
            lines.append(f"* REVIEWED NOTES: {_one_line(reviewed['notes'])}")
        lines.append("")
        record += count
    return "\n".join(lines)


def format_attributes(technical, fps):
    width, height = technical.get('width'), technical.get('height')
    if any(type(value) is not int or value <= 0 for value in (width, height)):
        raise ValueError('FCPXML timeline requires known positive source dimensions; use the media event instead.')
    if technical.get('rotation', 0) not in (None, 0):
        raise ValueError('FCPXML timeline cannot map rotated media yet; use the media event or Resolve import instead.')
    attributes = dict(frameDuration=f"{fps.denominator}/{fps.numerator}s", width=str(width), height=str(height))
    aspect = technical.get('sample_aspect_ratio')
    if aspect not in (None, '', 'N/A'):
        try:
            ratio = Fraction(str(aspect).replace(':', '/'))
            if ratio <= 0:
                raise ValueError()
        except (ValueError, ZeroDivisionError) as exc:
            raise ValueError('Invalid pixel aspect ratio; use the media event or Resolve import instead.') from exc
        attributes.update(paspH=str(ratio.numerator), paspV=str(ratio.denominator))
    return attributes


def export_fcpxml(job, *, preserve_order=False, title=None) -> str:
    """FCPXML 1.11 media references. Place original media beside XML or relink.

    Uses real asset-clip references and source dimensions/timing. No source
    path is leaked, no media is copied, and editor import is not assumed.
    Relative media URLs are documented by Apple:
    https://developer.apple.com/documentation/professional-video-applications/media-rep
    """
    data = job_data(job)
    fps, source_start, duration, timing_basis = _fcpxml_timing(data)
    technical = data.get("technical") or {}
    attributes = format_attributes(technical, fps)
    shots = _nle_shots(data, fps, duration, preserve_order=preserve_order)
    filename = source_filename(data.get("filename"))
    time = lambda frames: f"{frames * fps.denominator}/{fps.numerator}s"
    root = ET.Element("fcpxml", version="1.11")
    root.append(ET.Comment("Media reference export. Place the original file beside this XML or relink. Analysis boundaries are estimates rounded to source frames."))
    if timing_basis == "source_relative_file_zero":
        root.append(ET.Comment("Source-relative file-zero timing: no embedded source timecode was available. Zero is the media file origin, not an asserted original timecode."))
    resources = ET.SubElement(root, "resources")
    ET.SubElement(resources, "format", id="r1", **attributes)
    asset = ET.SubElement(resources, "asset", id="r2", name=filename, start=time(source_start),
                          duration=time(_frames(duration, fps)), hasVideo="1", format="r1",
                          hasAudio="1" if technical.get("has_audio") else "0")
    ET.SubElement(asset, "media-rep", kind="original-media", src=quote("./" + filename, safe="/"))
    asset_metadata = ET.SubElement(asset, "metadata")
    ET.SubElement(asset_metadata, "md", key="org.videoanalyzer.sourceTimingBasis", value=timing_basis)
    if technical.get("nominal_frame_rate_verified") is True:
        ET.SubElement(asset_metadata, "md", key="org.videoanalyzer.frameRateVerification",
                      value=technical.get("frame_rate_verification", ""))
    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event", name=f"{filename} Analysis")
    project = ET.SubElement(event, "project", name=title or f"{filename} Shot List")
    total = sum(last - first for first, last, *_ in shots)
    sequence = ET.SubElement(project, "sequence", format="r1", duration=time(total), tcStart="0s", tcFormat="NDF")
    spine = ET.SubElement(sequence, "spine")
    record = 0
    from app.services.editor_metadata import map_fields, effective_editor_shot
    for first, last, scene, shot, reviewed in shots:
        fields = map_fields(data.get('metadata') or {}, [effective_editor_shot(shot, reviewed)], [],
                            shot_scope=True, cosplay_primary=(data.get('analysis_config') or {}).get('mode') == 'cosplay')['fields']
        mapped = {field['key']: field['value'] for field in fields}
        count = last - first
        clip = ET.SubElement(spine, "asset-clip", ref="r2", name=f"S{scene.get('scene_number', '')}_Shot{shot.get('shot_number', '')}",
                             offset=time(record), start=time(source_start + first), duration=time(count), tcFormat="NDF")
        ET.SubElement(clip, "note").text = _one_line(
            f"{shot.get('shot_type', '')} | {shot.get('camera_movement', '')} | "
            f"{mapped.get('description', '')} | Reviewed notes: {reviewed.get('notes', '')}")
        for tag in next((field['xmp_value'] for field in fields if field['key'] == 'keywords'), []):
            ET.SubElement(clip, "keyword", start=time(source_start + first), duration=time(count), value=str(tag))
        if shot.get('slates'):
            metadata = ET.SubElement(clip, 'metadata')
            ET.SubElement(metadata, 'md', key='org.videoanalyzer.slates',
                          value=json.dumps(shot['slates'], ensure_ascii=False))
        record += count
    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n' + ET.tostring(root, encoding="unicode")
