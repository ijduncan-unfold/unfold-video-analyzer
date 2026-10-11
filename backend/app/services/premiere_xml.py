"""FCP7 xmeml v5 shot timelines for Premiere and Resolve; never copy media."""
import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote

from app.services.library_export import job_data, source_filename, segment_times, _public
from app.services.editor_metadata import map_fields
from app.services.nle_export import _fcpxml_timing, _frames, _frame_timecode, _nle_shots, _one_line


def source_url(path):
    """FCP7 file://localhost URLs, including Windows network and drive paths."""
    path = str(path or '').replace('\\', '/')
    if path.startswith('//') or path.startswith('/'):
        return quote('file://localhost' + path, safe='/:')
    if re.match(r'^[A-Za-z]:/', path):
        return quote('file://localhost/' + path, safe='/:')
    return None


def export_premiere_xml(job):
    data = job_data(job)
    fps, source_start, duration, basis = _fcpxml_timing(data)
    technical = data.get('technical') or {}
    width, height = technical.get('width'), technical.get('height')
    if any(type(value) is not int or value <= 0 for value in (width, height)):
        raise ValueError('Premiere XML requires known source dimensions')
    if technical.get('rotation', 0) not in (None, 0):
        raise ValueError('Premiere XML requires a verified orientation mapping for rotated media')
    if technical.get('sample_aspect_ratio') not in (None, '', '1:1', '1/1', 'N/A'):
        raise ValueError('Premiere XML requires a verified pixel aspect mapping for non-square media')
    channels = technical.get('audio_channels') or []
    if technical.get('has_audio') and (not channels or any(type(value) is not int or value < 1 for value in channels)):
        raise ValueError('Premiere XML needs the original audio channel layout. Open the linked source to refresh its media metadata.')
    channel_count = sum(channels) if technical.get('has_audio') else 0
    if channel_count > 32:
        raise ValueError('Premiere XML supports up to 32 original audio channels')
    shots = _nle_shots(data, fps, duration)
    nominal = (fps.numerator + fps.denominator - 1) // fps.denominator
    source_frames = _frames(duration, fps)
    total = sum(last - first for first, last, *_ in shots)
    filename = source_filename(data.get('filename'))

    def text(parent, tag, value):
        ET.SubElement(parent, tag).text = str(value)

    def rate(parent):
        element = ET.SubElement(parent, 'rate')
        text(element, 'timebase', nominal)
        text(element, 'ntsc', 'TRUE' if fps.denominator == 1001 else 'FALSE')

    root = ET.Element('xmeml', version='5')
    root.append(ET.Comment('References original media, not analysis proxies. Analysis boundaries are estimates rounded to source frames.'))
    if basis == 'source_relative_file_zero':
        root.append(ET.Comment('No embedded source timecode: file-relative zero is used; it is not an asserted camera timecode.'))
    sequence = ET.SubElement(root, 'sequence', id='va-sequence')
    text(sequence, 'name', f'{filename} Shot List')
    text(sequence, 'duration', total)
    rate(sequence)
    tc = ET.SubElement(sequence, 'timecode')
    rate(tc)
    text(tc, 'string', '00:00:00:00'); text(tc, 'frame', 0); text(tc, 'displayformat', 'NDF')
    media = ET.SubElement(sequence, 'media')
    video = ET.SubElement(media, 'video')
    sample = ET.SubElement(ET.SubElement(video, 'format'), 'samplecharacteristics')
    rate(sample)
    text(sample, 'width', width); text(sample, 'height', height)
    text(sample, 'anamorphic', 'FALSE'); text(sample, 'pixelaspectratio', 'square')
    video_track = ET.SubElement(video, 'track')
    audio = ET.SubElement(media, 'audio') if channel_count else None
    audio_tracks = [ET.SubElement(audio, 'track') for _ in range(channel_count)]
    record = 0
    for index, (first, last, scene, shot, reviewed) in enumerate(shots, 1):
        shot_start, shot_end = segment_times(shot, duration)
        dialogue = []
        for cue in data.get('transcript') or []:
            try:
                start, end = segment_times(cue, duration)
                if start < shot_end and end > shot_start:
                    dialogue.append(cue)
            except (ValueError, AttributeError):
                continue
        section_numbers = {scene.get('scene_number'), *(shot.get('section_numbers') or [])}
        section_analysis = [section for section in data.get('deep_results') or []
                            if section.get('scene_number') in section_numbers]
        ids = [f'va-shot-{index}-v'] + [f'va-shot-{index}-a{channel + 1}' for channel in range(channel_count)]
        for track_index, track in enumerate([video_track, *audio_tracks]):
            item = ET.SubElement(track, 'clipitem', id=ids[track_index])
            text(item, 'name', f'Shot {shot.get("shot_number", index):02d}')
            text(item, 'duration', source_frames)
            rate(item)
            for tag, value in [('start', record), ('end', record + last - first), ('in', first), ('out', last)]:
                text(item, tag, value)
            text(item, 'enabled', 'TRUE')
            source = ET.SubElement(item, 'file', id='va-original')
            if index == 1 and track_index == 0:
                text(source, 'name', filename)
                url = source_url(data.get('local_path'))
                if url:
                    text(source, 'pathurl', url)
                text(source, 'duration', source_frames)
                rate(source)
                timecode = ET.SubElement(source, 'timecode')
                rate(timecode)
                text(timecode, 'string', _frame_timecode(source_start, nominal))
                text(timecode, 'frame', source_start); text(timecode, 'displayformat', 'NDF')
                source_media = ET.SubElement(source, 'media')
                source_video = ET.SubElement(source_media, 'video')
                text(source_video, 'duration', source_frames)
                source_video.append(ET.fromstring(ET.tostring(sample)))
                if channel_count:
                    source_audio = ET.SubElement(source_media, 'audio')
                    text(source_audio, 'channelcount', channel_count)
                    rates = technical.get('audio_sample_rates') or []
                    if rates and len(set(rates)) == 1 and type(rates[0]) is int and rates[0] > 0:
                        text(ET.SubElement(source_audio, 'samplecharacteristics'), 'samplerate', rates[0])
            source_track = ET.SubElement(item, 'sourcetrack')
            text(source_track, 'mediatype', 'video' if track_index == 0 else 'audio')
            text(source_track, 'trackindex', max(1, track_index))
            for linked_index, linked_id in enumerate(ids):
                link = ET.SubElement(item, 'link')
                text(link, 'linkclipref', linked_id)
                text(link, 'mediatype', 'video' if linked_index == 0 else 'audio')
                text(link, 'trackindex', max(1, linked_index)); text(link, 'clipindex', index)
            if track_index == 0:
                logging = ET.SubElement(item, 'logginginfo')
                text(logging, 'description', _one_line(shot.get('visual_description')))
                mapped = {field['key']: field['value'] for field in map_fields(
                    data.get('metadata') or {}, [shot], dialogue, shot_scope=True)['fields']}
                if mapped.get('scene'):
                    text(logging, 'scene', mapped['scene'])
                if mapped.get('shot') or mapped.get('take'):
                    text(logging, 'shottake', ' / '.join(mapped[key] for key in ('shot', 'take') if mapped.get(key)))
                text(logging, 'lognote', json.dumps(_public({'shot': shot, 'annotation': reviewed,
                    'asset_metadata': data.get('metadata', {}), 'section': scene.get('scene_title'),
                    'section_analysis': section_analysis, 'transcript': dialogue}), ensure_ascii=False))
        record += last - first
    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + ET.tostring(root, encoding='unicode')
