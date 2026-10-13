"""Multiple original media references in an FCP7 project for Premiere/Resolve."""
import copy
import csv
import io
import json
import re
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from fastapi import HTTPException
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


EXPORT_FILES = {
    'premiere_xml': ('Video Analyzer.xml', 'application/xml'),
    'fcpxml': ('Video Analyzer.fcpxml', 'application/xml'),
    'edl': ('Video Analyzer.edl', 'text/plain'),
    'resolve': ('Import into Resolve.py', 'text/x-python'),
}


def export_content(jobs, title, layout, format):
    from app.services.alternate_media_export import combined_fcpxml, combined_edl, resolve_script
    if format == 'fcpxml':
        return combined_fcpxml(jobs, title, layout)
    if format == 'resolve':
        return resolve_script(jobs, title)
    if format == 'edl':
        return combined_edl(jobs, title)
    if format != 'premiere_xml':
        raise ValueError('Unknown media export format')
    return combined_project(jobs, title, layout)


def export_checks(jobs, format='premiere_xml', layout='timeline'):
    items = []
    for job in jobs:
        reason = None
        try:
            export_content([job], 'Selection', layout, format)
        except (ValueError, OSError, HTTPException) as exc:
            reason = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
        items.append({'id': job.job_id, 'filename': source_filename(job.filename),
                      'title': job.metadata.get('title') or source_filename(job.filename),
                      'available': reason is None, 'reason': reason,
                      'analysis_version': str(job.analysis_config.get('started_at') or ''),
                      'analyzed': bool(job.flash_result),
                      'partial': job.status in ('queued', 'analyzing', 'processing', 'error')})
    collection_reason = None
    if format == 'edl' and all(item['available'] for item in items):
        try:
            export_content(jobs, 'Selection', layout, format)
        except ValueError as exc:
            collection_reason = str(exc)
    return {'items': items, 'available': all(item['available'] for item in items) and not collection_reason,
            'reason': collection_reason, 'format': format, 'layout': layout}


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


def legacy_bin_master(parent, job, index):
    """Whole-file FCP7 master clips, without timeline interpretation overrides.

    Legacy XML needs rate/duration declarations even for bin clips. Use the
    source's reported nominal rate for those declarations, never as proof of
    constant cadence. Premiere probes the actual file on import. The clip spans
    the whole source; only verified timing may be used for analysis markers.
    """
    from fractions import Fraction
    from app.services.library_export import parse_seconds
    technical = job.technical
    url = source_url(job.local_path)
    if not url:
        raise ValueError('Original media has no absolute file reference. Relink it before exporting.')
    raw_rate = technical.get('nominal_frame_rate_fraction') or technical.get('frame_rate_fraction') or technical.get('frame_rate')
    try:
        fps = Fraction(str(raw_rate))
    except (ValueError, ZeroDivisionError):
        raise ValueError('Legacy XML needs a reported source frame rate. Use the Premiere panel for native import.')
    supported = {Fraction(n) for n in (12, 15, 24, 25, 30, 48, 50, 60, 100, 120)} | {Fraction(n, 1001) for n in (24000, 30000, 48000, 60000, 120000)}
    if fps not in supported:
        raise ValueError('Legacy XML cannot represent this reported frame rate. Use the Premiere panel for native import.')
    duration = parse_seconds(technical.get('duration_seconds'))
    count = _frames(duration, fps)
    if count <= 0:
        raise ValueError('Legacy XML needs the original media duration. Refresh the source metadata or use the Premiere panel.')
    channels = technical.get('audio_channels') or []
    if technical.get('has_audio') and (not channels or any(type(n) is not int or n <= 0 for n in channels)):
        raise ValueError('Legacy XML needs the original audio channel layout. Refresh the source metadata or use the Premiere panel.')
    channel_count = sum(channels) if technical.get('has_audio') else 0
    if channel_count > 32:
        raise ValueError('Legacy XML supports up to 32 original audio channels.')
    prefix = f'va-media-{index}'
    master_id = prefix + '-master'
    name = job.metadata.get('title') or source_filename(job.filename)
    def rate(parent):
        element = ET.SubElement(parent, 'rate')
        text(element, 'timebase', (fps.numerator + fps.denominator - 1) // fps.denominator)
        text(element, 'ntsc', 'TRUE' if fps.denominator == 1001 else 'FALSE')
    master = ET.SubElement(parent, 'clip', id=master_id)
    text(master, 'name', name); text(master, 'duration', count); rate(master)
    text(master, 'masterclipid', master_id); text(master, 'ismasterclip', 'TRUE')
    logging(master, job)
    try:
        marker_fps, _, _, _ = _fcpxml_timing(job_data(job))
    except ValueError:
        pass
    else:
        markers(master, job, marker_fps)
    media = ET.SubElement(master, 'media')
    video = ET.SubElement(media, 'video')
    sample = ET.SubElement(ET.SubElement(video, 'format'), 'samplecharacteristics')
    rate(sample)
    for key in ('width', 'height'):
        value = technical.get(key)
        if type(value) is int and value > 0:
            text(sample, key, value)
    tracks = [ET.SubElement(video, 'track')]
    if channel_count:
        audio = ET.SubElement(media, 'audio')
        tracks.extend(ET.SubElement(audio, 'track') for _ in range(channel_count))
    ids = [prefix + '-v'] + [f'{prefix}-a{n + 1}' for n in range(channel_count)]
    for n, track in enumerate(tracks):
        item = ET.SubElement(track, 'clipitem', id=ids[n])
        text(item, 'name', name); text(item, 'duration', count); rate(item)
        for tag in ('start', 'end'):
            text(item, tag, -1)
        text(item, 'in', 0); text(item, 'out', count)
        text(item, 'enabled', 'TRUE')
        file = ET.SubElement(item, 'file', id=prefix + '-file')
        if n == 0:
            text(file, 'name', source_filename(job.filename)); text(file, 'pathurl', url)
            text(file, 'duration', count); rate(file)
            file_media = ET.SubElement(file, 'media')
            file_video = ET.SubElement(file_media, 'video')
            text(file_video, 'duration', count)
            file_video.append(copy.deepcopy(sample))
            if channel_count:
                text(ET.SubElement(file_media, 'audio'), 'channelcount', channel_count)
        source_track = ET.SubElement(item, 'sourcetrack')
        text(source_track, 'mediatype', 'video' if n == 0 else 'audio')
        text(source_track, 'trackindex', max(1, n))
        for linked_index, linked_id in enumerate(ids):
            link = ET.SubElement(item, 'link')
            text(link, 'linkclipref', linked_id)
            text(link, 'mediatype', 'video' if linked_index == 0 else 'audio')
            text(link, 'trackindex', max(1, linked_index)); text(link, 'clipindex', 1)


def combined_project(jobs, title, layout='timeline'):
    root = ET.Element('xmeml', version='5')
    root.append(ET.Comment('Original media references only. Full source durations; analysis appears as markers and logging.'))
    project = ET.SubElement(root, 'project')
    text(project, 'name', title)
    children = ET.SubElement(project, 'children')
    bin_element = ET.SubElement(children, 'bin')
    text(bin_element, 'name', title)
    bin_children = ET.SubElement(bin_element, 'children')
    sequence = None
    timeline_rate = None
    record = 0
    for index, job in enumerate(jobs, 1):
        data = job_data(job)
        if layout == 'bin':
            legacy_bin_master(bin_children, job, index)
            continue
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


def resolve_metadata_csv(jobs):
    """One row per original for Resolve's File > Import Metadata To workflow.

    Clip Directory disambiguates repeated camera filenames; unlike clips.csv,
    this is media-level metadata with Resolve's column names.
    """
    from pathlib import PurePosixPath, PureWindowsPath
    from app.services.resolve_import_template import mapped_values
    columns = ['File Name', 'Clip Directory', 'Description', 'Keywords', 'Comments',
               'Scene', 'Shot', 'Take', 'Camera #', 'Roll Card #', 'Production Name',
               'Director', 'DOP', 'Shot Type', 'Move', 'Location']
    output = io.StringIO(newline='')
    output.write('\ufeff')
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    for job in jobs:
        mapping = editor_metadata(job)
        path = PureWindowsPath(job.local_path) if re.match(r'^(?:[A-Za-z]:|\\\\)', job.local_path) else PurePosixPath(job.local_path)
        keywords = next((field['xmp_value'] for field in mapping['fields'] if field['key'] == 'keywords'), [])
        writer.writerow({**mapped_values(mapping), 'File Name': path.name,
            'Clip Directory': str(path.parent), 'Keywords': ', '.join(keywords),
            'Comments': '\n'.join(field['label'] + ': ' + field['value'] for field in mapping['fields'])})
    return output.getvalue()


def build_download(jobs, title, layout, include_metadata, format='premiere_xml'):
    content = export_content(jobs, title, layout, format)
    main_file, _ = EXPORT_FILES[format]
    temporary = tempfile.NamedTemporaryFile(prefix='video-analyzer-export-', suffix='.zip' if include_metadata else Path(main_file).suffix, delete=False)
    path = Path(temporary.name)
    temporary.close()
    try:
        if not include_metadata:
            path.write_text(content, encoding='utf-8')
            return path
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(main_file, content)
            archive.writestr('library.json', json.dumps({'schema': 'video-analyzer.collection', 'schema_version': '1.0',
                'assets': [portable_document(job) for job in jobs]}, ensure_ascii=False, indent=2))
            archive.writestr('clips.csv', combined_csv(jobs))
            archive.writestr('Resolve metadata.csv', resolve_metadata_csv(jobs))
            archive.writestr('Metadata import.txt',
                'The XMP sidecars contain standard Dublin Core title, description and subject (keywords), including cosplay character/franchise/variant and the general scene description. They are portable metadata files, not automatically applied to your original videos.\n\n'
                'PREMIERE: Import the legacy Video Analyzer.xml to get searchable clip Description and Log Note. File > Dublin Core is separate file metadata. Populating that section requires applying XMP through a compatible metadata writer or the native Video Analyzer Premiere panel; this ZIP does not rewrite source files.\n\n'
                'RESOLVE: Import the original media, then choose File > Import Metadata To > Media Pool and select Resolve metadata.csv. Match using filename AND source file path (Clip Directory), especially when camera filenames repeat. Disable clip start/end timecode matching: this CSV intentionally has no invented timecodes. Choose Only update metadata items with entries in the source file. Inspect the import count and Description/Keywords fields. If you moved the footage, update Clip Directory to its new location first; do not use filename-only matching with duplicate names. Importing may replace existing values in matching fields; use the native Resolve import option to preserve populated fields and merge keywords.\n\n'
                'The native Resolve import option imports the originals and applies this same metadata through Resolve directly. Neither workflow depends on Resolve automatically reading Adobe XMP sidecars. Resolve native acceptance is still pending on this machine.\n')
            records = []
            for index, job in enumerate(jobs, 1):
                stem = re.sub(r'[^\w .-]', '_', Path(source_filename(job.filename)).stem)[:100].strip(' .') or 'media'
                folder = f'metadata/{index:04d}_{stem}'
                record = {'id': job.job_id, 'filename': source_filename(job.filename), 'files': [], 'unavailable': {}}
                for sidecar_format in ('xmp', 'srt'):
                    try:
                        result, _, extension = export_library(job, sidecar_format)
                    except ValueError as exc:
                        record['unavailable'][sidecar_format] = str(exc)
                        continue
                    name = f'{folder}/{stem}.{extension}'
                    archive.writestr(name, result)
                    record['files'].append(name)
                records.append(record)
            archive.writestr('manifest.json', json.dumps({'media_count': len(jobs), 'media_included': False, 'items': records}, ensure_ascii=False, indent=2))
            instructions = {
                'premiere_xml': 'LEGACY Final Cut Pro 7 XML (.xml), for Premiere / Resolve. In Premiere choose File > Import and select Video Analyzer.xml (extract the ZIP first). Whole original clips appear in a named bin with their source audio. Character, franchise, costume and scene descriptions are carried in clip Description and Log Note. In Premiere use the Project panel search (All metadata); show Description and Log Note columns using Metadata Display, or inspect the Clip section of the Metadata panel. These are project clip fields, not File > Dublin Core metadata; original videos are not rewritten. Modern .fcpxml is a different format and cannot be imported directly by Premiere. Bin exports declare the reported source rate and duration required by legacy XML; Premiere reads native interpretation from the original. Timed markers are included only when source timing is reliable. The optional timeline requires verified timing and follows selection order.',
                'fcpxml': 'Import Video Analyzer.fcpxml into Final Cut Pro (File > Import > XML). For a timeline, Resolve also accepts FCPXML via File > Import > Timeline. Media-event-only import is intended for Final Cut; use the Resolve package for a Resolve bin. Event-only exports let the editor discover native duration, cadence and aspect ratio. Timeline exports carry exact source rates and explicit known pixel aspect ratios. Notes and keywords contain reviewed descriptions and tags.',
                'resolve': "Open the intended project in DaVinci Resolve. Extract Import into Resolve.py. Open Workspace > Console, select Python 3, and execute: exec(open('/absolute/path/Import into Resolve.py', encoding='utf-8').read()). Substitute the actual extracted script path; Windows can use forward slashes. Requires a working Resolve Python scripting runtime. The script imports whole originals into a named Media Pool bin, applies Description/Keywords and full analysis in Comments, preserves existing populated fields, and verifies the writes. It does not create a timeline. Native codec support is determined by Resolve and its version/platform. Rerunning reuses the selection bin and preserves editor changes. If a source changed, export a fresh package; no silent skips.",
                'edl': 'Import original media into your editor, then conform Video Analyzer.edl as a CMX3600 EDL. Video only: audio, codecs and pixel aspect are not encoded. Assign/map the generated VA000001 etc. reel IDs using EDL reels.csv (source paths also appear in comments); duplicate basenames require explicit reel mapping. All events use one verified non-drop rate and known embedded timecode. Comments are not a substitute for native searchable metadata; retain the accompanying metadata files.',
            }
            if format == 'edl':
                reels = io.StringIO(newline='')
                writer = csv.writer(reels)
                writer.writerow(['Reel', 'File Name', 'File Path'])
                writer.writerows((f'VA{index:06d}', source_filename(job.filename), job.local_path) for index, job in enumerate(jobs, 1))
                archive.writestr('EDL reels.csv', reels.getvalue())
            archive.writestr('Read me.txt', instructions[format] + '\n\nOriginal files are referenced in place, not copied or rendered. Keep their drives mounted, preserve camera sidecars and folder structure, and relink if moved. Format compatibility does not add RAW decoder support to the receiving editor.\n\nThe package retains complete saved analysis in library.json and XMP, plus combined CSV and available source-relative SRT. Editor metadata exposure varies by importer. manifest.json lists unavailable sidecars. No new analysis is performed. These handoffs contain original filesystem paths.\n')
        return path
    except BaseException:
        path.unlink(missing_ok=True)
        raise
