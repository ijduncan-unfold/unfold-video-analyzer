"""Multiple original media references in an FCP7 project for Premiere/Resolve."""
import copy
import csv
import io
import json
import re
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from app.services.library_export import export_library, iter_shots, job_data, portable_document, segment_times, source_filename
from app.services.editor_metadata import editor_metadata, effective_editor_shot, map_fields
from app.services.nle_export import _fcpxml_timing, _frames
from app.services.premiere_xml import export_premiere_xml, source_url


def text(parent, tag, value):
    ET.SubElement(parent, tag).text = str(value)


def full_media(job):
    data = job_data(job)
    _, _, duration, _ = _fcpxml_timing(data)
    # Whole originals, not the detected subclips. Keep analysis as logging/markers.
    data['flash_result'] = {'scenes': [{'scene_number': 1, 'shots': [{
        'shot_number': 1, 'start_time': '0', 'end_time': str(duration),
        'visual_description': '', 'tags': [], 'shot_type': '', 'camera_movement': '',
    }]}]}
    return data


def source_project(job):
    if not source_url(job.local_path):
        raise ValueError('Original media has no absolute file reference. Relink it before exporting.')
    return ET.fromstring(export_premiere_xml(full_media(job)))


def export_checks(jobs):
    items = []
    for job in jobs:
        reason = None
        try:
            source_project(job)
        except ValueError as exc:
            reason = str(exc)
        items.append({'id': job.job_id, 'filename': source_filename(job.filename),
                      'title': job.metadata.get('title') or source_filename(job.filename),
                      'available': reason is None, 'reason': reason,
                      'analysis_version': str(job.analysis_config.get('started_at') or ''),
                      'analyzed': bool(job.flash_result),
                      'partial': job.status in ('queued', 'analyzing', 'processing', 'error')})
    return {'items': items, 'available': all(item['available'] for item in items)}


def logging(parent, job):
    mapped = editor_metadata(job, include_record=True)
    fields = {field['key']: field['value'] for field in mapped['fields']}
    description = fields.get('description') or '\n'.join(
        shot.get('visual_description', '') for _, shot, _ in iter_shots(job_data(job)))
    element = ET.SubElement(parent, 'logginginfo')
    text(element, 'description', description)
    if fields.get('scene'):
        text(element, 'scene', fields['scene'])
    if fields.get('shot') or fields.get('take'):
        text(element, 'shottake', ' / '.join(fields[key] for key in ('shot', 'take') if fields.get(key)))
    readable = '\n'.join(f"{field['label']}: {field['value']}" for field in mapped['fields'])
    text(element, 'lognote', readable + '\n\nVideo Analyzer analysis:\n' + json.dumps(mapped['record'], ensure_ascii=False))


def markers(parent, job, fps):
    data = job_data(job)
    duration = data['technical']['duration_seconds']
    observations = []
    for _, shot, annotation in iter_shots(data):
        fields = map_fields(data.get('metadata') or {}, [effective_editor_shot(shot, annotation)], [],
                            shot_scope=True, cosplay_primary=(data.get('analysis_config') or {}).get('mode') == 'cosplay')['fields']
        description = next((field['value'] for field in fields if field['key'] == 'description'), '')
        observations.append((f"Clip {shot.get('shot_number')}", shot, description))
    observations += [('Dialogue', cue, cue.get('text', '')) for cue in data.get('transcript') or []]
    for name, observation, note in observations:
        start, end = segment_times(observation, duration)
        first, last = _frames(start, fps), _frames(end, fps)
        if last <= first:
            continue
        marker = ET.SubElement(parent, 'marker')
        text(marker, 'name', name); text(marker, 'comment', note)
        text(marker, 'in', first); text(marker, 'out', last)


def combined_project(jobs, title, layout='timeline'):
    root = ET.Element('xmeml', version='5')
    root.append(ET.Comment('Original media references only. Full source durations; analysis appears as markers and logging.'))
    project = ET.SubElement(root, 'project')
    text(project, 'name', title)
    children = ET.SubElement(project, 'children')
    bin_element = ET.SubElement(children, 'bin')
    text(bin_element, 'name', 'Analyzed media')
    bin_children = ET.SubElement(bin_element, 'children')
    sequence = None
    timeline_rate = None
    record = 0
    for index, job in enumerate(jobs, 1):
        data = job_data(job)
        fps, _, duration, _ = _fcpxml_timing(data)
        source_seq = source_project(job).find('sequence')
        prefix = f'va-media-{index}'
        for element in source_seq.iter():
            if element.get('id'):
                element.set('id', prefix + '-' + element.get('id'))
            if element.tag == 'linkclipref':
                element.text = prefix + '-' + element.text
        filename = source_filename(job.filename)
        name = job.metadata.get('title') or filename
        master_id = prefix + '-master'
        master = ET.SubElement(bin_children, 'clip', id=master_id)
        text(master, 'name', name); text(master, 'duration', _frames(duration, fps))
        master.append(copy.deepcopy(source_seq.find('rate')))
        text(master, 'masterclipid', master_id); text(master, 'ismasterclip', 'TRUE')
        logging(master, job); markers(master, job, fps)
        master_media = copy.deepcopy(source_seq.find('media'))
        for item in master_media.findall('.//clipitem'):
            item.set('id', item.get('id') + '-master')
            item.find('name').text = name
            for tag in ('start', 'end'):
                item.find(tag).text = '-1'
            for link in item.findall('link'):
                ref = link.find('linkclipref'); ref.text += '-master'
            old_logging = item.find('logginginfo')
            if old_logging is not None:
                item.remove(old_logging)
        master.append(master_media)
        if layout == 'bin':
            continue
        if sequence is None:
            sequence = ET.SubElement(children, 'sequence', id='va-selected-timeline')
            text(sequence, 'name', title)
            text(sequence, 'duration', 0)
            sequence.append(copy.deepcopy(source_seq.find('rate')))
            sequence.append(copy.deepcopy(source_seq.find('timecode')))
            timeline_rate = fps
            media = ET.SubElement(sequence, 'media')
            video = ET.SubElement(media, 'video')
            video.append(copy.deepcopy(source_seq.find('media/video/format')))
            ET.SubElement(video, 'track')
        # Timeline start/end use sequence frames; in/out remain source frames.
        count = _frames(duration, timeline_rate)
        if count <= 0:
            raise ValueError(f'{filename}: source is shorter than a timeline frame')
        source_tracks = [source_seq.find('media/video/track'), *source_seq.findall('media/audio/track')]
        destination_tracks = [sequence.find('media/video/track')]
        if len(source_tracks) > 1:
            audio = sequence.find('media/audio')
            if audio is None:
                audio = ET.SubElement(sequence.find('media'), 'audio')
            while len(audio.findall('track')) < len(source_tracks) - 1:
                ET.SubElement(audio, 'track')
            destination_tracks.extend(audio.findall('track')[:len(source_tracks) - 1])
        clip_indices = [len(track.findall('clipitem')) + 1 for track in destination_tracks]
        for track_index, source_track in enumerate(source_tracks):
            if track_index == 0:
                track = sequence.find('media/video/track')
            else:
                audio = sequence.find('media/audio')
                if audio is None:
                    audio = ET.SubElement(sequence.find('media'), 'audio')
                while len(audio.findall('track')) < track_index:
                    ET.SubElement(audio, 'track')
                track = audio.findall('track')[track_index - 1]
            item = source_track.find('clipitem')
            item.find('name').text = name
            text(item, 'masterclipid', master_id)
            item.find('start').text = str(record); item.find('end').text = str(record + count)
            # Source definitions are carried by the master media in the bin.
            file = item.find('file')
            for child in list(file):
                file.remove(child)
            for linked_index, link in enumerate(item.findall('link')):
                link.find('clipindex').text = str(clip_indices[linked_index])
            old_logging = item.find('logginginfo')
            if old_logging is not None:
                item.remove(old_logging)
            if track_index == 0:
                logging(item, job); markers(item, job, fps)
            track.append(item)
        record += count
    if sequence is not None:
        sequence.find('duration').text = str(record)
    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + ET.tostring(root, encoding='unicode')


def combined_csv(jobs):
    output = io.StringIO(newline='')
    output.write('\ufeff')
    writer = csv.writer(output)
    for index, job in enumerate(jobs):
        rows = list(csv.reader(io.StringIO(export_library(job, 'csv')[0].lstrip('\ufeff'))))
        writer.writerows(rows if index == 0 else rows[1:])
    return output.getvalue()


def build_download(jobs, title, layout, include_metadata):
    content = combined_project(jobs, title, layout)
    temporary = tempfile.NamedTemporaryFile(prefix='video-analyzer-export-', suffix='.zip' if include_metadata else '.xml', delete=False)
    path = Path(temporary.name)
    temporary.close()
    try:
        if not include_metadata:
            path.write_text(content, encoding='utf-8')
            return path
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('Video Analyzer.xml', content)
            archive.writestr('library.json', json.dumps({'schema': 'video-analyzer.collection', 'schema_version': '1.0',
                'assets': [portable_document(job) for job in jobs]}, ensure_ascii=False, indent=2))
            archive.writestr('clips.csv', combined_csv(jobs))
            records = []
            for index, job in enumerate(jobs, 1):
                stem = re.sub(r'[^\w .-]', '_', Path(source_filename(job.filename)).stem)[:100].strip(' .') or 'media'
                folder = f'metadata/{index:04d}_{stem}'
                record = {'id': job.job_id, 'filename': source_filename(job.filename), 'files': [], 'unavailable': {}}
                for format in ('xmp', 'srt'):
                    try:
                        result, _, extension = export_library(job, format)
                    except ValueError as exc:
                        record['unavailable'][format] = str(exc)
                        continue
                    name = f'{folder}/{stem}.{extension}'
                    archive.writestr(name, result)
                    record['files'].append(name)
                records.append(record)
            archive.writestr('manifest.json', json.dumps({'media_count': len(jobs), 'media_included': False, 'items': records}, ensure_ascii=False, indent=2))
            archive.writestr('Read me.txt', 'Extract this package, then import Video Analyzer.xml into Premiere (File > Import) or Resolve (File > Import > Timeline). The media bin contains whole original recordings; the optional timeline follows your selection order. Original video is referenced, not copied. Relink if a source has moved or is offline.\n\nDescriptions, source-relative analysis/dialogue markers and logging notes are in the XML. Editors differ in which XML metadata fields they expose. library.json and the XMP sidecars retain complete saved analysis; use the editor integration to sync supported native metadata fields. Subtitle files retain source-relative times, not combined-timeline times. manifest.json lists absent/invalid subtitle exports. AI timestamps are approximate. Mixed frame-rate timeline lengths round to sequence frames without changing the source in/out points.\n')
        return path
    except BaseException:
        path.unlink(missing_ok=True)
        raise
