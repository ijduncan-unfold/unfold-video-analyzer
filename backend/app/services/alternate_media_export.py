"""Whole-original interchange routes with format-specific requirements."""
import copy
import json
from fractions import Fraction
from pathlib import Path
import xml.etree.ElementTree as ET

from app.services.editor_metadata import editor_metadata
from app.services.library_export import job_data, source_filename
from app.services.nle_export import _fcpxml_timing, _source_timing, _frames, _frame_timecode, _one_line, format_attributes
from app.services.premiere_xml import source_url


def original_url(job):
    url = source_url(job.local_path)
    if not url:
        raise ValueError('Original media has no absolute file reference. Relink it before exporting.')
    return url


def rational_time(value):
    value = Fraction(value)
    return f'{value.numerator}/{value.denominator}s'



def combined_fcpxml(jobs, title, layout='timeline'):
    # Apple FCPXML 1.10 DTD: asset duration/format and asset-clip duration are
    # optional. Media events intentionally let the editor inspect native files.
    root = ET.Element('fcpxml', version='1.10')
    resources = ET.SubElement(root, 'resources')
    event = ET.SubElement(ET.SubElement(root, 'library'), 'event', name=title)
    spine, sequence, timeline_fps, record = None, None, None, 0
    for index, job in enumerate(jobs, 1):
        url = original_url(job)
        mapped = editor_metadata(job, include_record=True)
        name = job.metadata.get('title') or source_filename(job.filename)
        asset_id, format_id = f'a{index}', f'f{index}'
        attrs = dict(id=asset_id, name=source_filename(job.filename))
        clip_attrs = dict(ref=asset_id, name=name)
        if layout == 'timeline':
            fps, source_start, duration, _ = _fcpxml_timing(job_data(job))
            ET.SubElement(resources, 'format', id=format_id, **format_attributes(job.technical, fps))
            count = _frames(duration, fps)
            if count <= 0:
                raise ValueError('Source is shorter than one frame.')
            attrs.update(format=format_id, start=rational_time(source_start / fps), duration=rational_time(count / fps))
            clip_attrs.update(format=format_id, start=attrs['start'], duration=attrs['duration'], tcFormat='NDF')
            if sequence is None:
                timeline_fps = fps
                project = ET.SubElement(event, 'project', name=title)
                sequence = ET.SubElement(project, 'sequence', format=format_id, tcStart='0s', tcFormat='NDF')
                spine = ET.SubElement(sequence, 'spine')
        asset = ET.SubElement(resources, 'asset', **attrs)
        ET.SubElement(asset, 'media-rep', kind='original-media', src=url)
        metadata = ET.SubElement(asset, 'metadata')
        for field in mapped['fields']:
            ET.SubElement(metadata, 'md', key='org.videoanalyzer.' + field['key'], value=field['value'])
        ET.SubElement(metadata, 'md', key='org.videoanalyzer.analysis', value=json.dumps(mapped['record'], ensure_ascii=False))
        clip = ET.SubElement(event, 'asset-clip', **clip_attrs)
        ET.SubElement(clip, 'note').text = '\n'.join(f"{field['label']}: {field['value']}" for field in mapped['fields'])
        keywords = next((field.get('xmp_value', []) for field in mapped['fields'] if field['key'] == 'keywords'), [])
        for keyword in keywords:
            ET.SubElement(clip, 'keyword', value=str(keyword))
        if spine is not None:
            placed = copy.deepcopy(clip)
            placed.set('offset', rational_time(Fraction(record, 1) / timeline_fps))
            spine.append(placed)
            record += _frames(Fraction(count, 1) / fps, timeline_fps)
    if sequence is not None:
        sequence.set('duration', rational_time(Fraction(record, 1) / timeline_fps))
    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n' + ET.tostring(root, encoding='unicode')


def combined_edl(jobs, title):
    if len(jobs) > 999:
        raise ValueError('CMX3600 EDL is limited to 999 recordings. Split this selection.')
    lines = [f'TITLE: {_one_line(title)}', 'FCM: NON-DROP FRAME',
             '* VIDEO ONLY. IMPORT ORIGINAL MEDIA SEPARATELY AND CONFORM BY REEL/TIMECODE.',
             '* FULL METADATA IS IN THE OPTIONAL PACKAGE; EDL COMMENTS ARE NOT NATIVE METADATA.', '']
    rate, record = None, 0
    for index, job in enumerate(jobs, 1):
        original_url(job)
        fps, start, duration = _source_timing(job_data(job))
        if fps not in {Fraction(24), Fraction(25), Fraction(30), Fraction(24000, 1001), Fraction(30000, 1001)}:
            raise ValueError('CMX3600 supports 24, 25, 30, 23.976 or 29.97 fps non-drop sources.')
        if rate is not None and fps != rate:
            raise ValueError('EDL requires one frame rate across the selection. Use FCPXML or Resolve import for mixed rates.')
        rate = fps
        nominal = (fps.numerator + fps.denominator - 1) // fps.denominator
        count = _frames(duration, fps)
        if count <= 0:
            raise ValueError('Source is shorter than one frame.')
        reel = f'VA{index:06d}'
        tc = lambda value: _frame_timecode(value, nominal)
        lines.append(f'{index:03d}  {reel} V     C        {tc(start)} {tc(start + count)} {tc(record)} {tc(record + count)}')
        fields = {field['key']: field['value'] for field in editor_metadata(job)['fields']}
        lines.extend([f'* FROM CLIP NAME: {_one_line(source_filename(job.filename))}',
                      f'* SOURCE FILE: {_one_line(job.local_path)}', f'* SOURCE FRAME RATE: {fps}',
                      f'* COMMENT: {_one_line(fields.get("description"))}', f'* KEYWORDS: {_one_line(fields.get("keywords"))}', ''])
        record += count
    return '\n'.join(lines)


def resolve_payload(jobs, title):
    from app.services.editor_bridge import ensure_original_unchanged
    items = []
    for job in jobs:
        original_url(job)
        path = Path(job.local_path)
        if not path.is_file():
            raise ValueError(f'{source_filename(job.filename)}: original is offline. Reconnect it before packaging native import.')
        ensure_original_unchanged(job)
        stat = path.stat()
        items.append(dict(id=job.job_id, path=str(path), size=stat.st_size, mtime_ns=stat.st_mtime_ns,
                          mapping=editor_metadata(job, include_record=True)))
    if len({item['path'] for item in items}) != len(items):
        raise ValueError('Selection contains duplicate original paths. Select each original once.')
    return dict(schema='unfold.resolve-import.v1', title=title, items=items)


def resolve_script(jobs, title):
    import base64
    encoded = base64.b64encode(json.dumps(resolve_payload(jobs, title), ensure_ascii=False).encode()).decode('ascii')
    # Data is never interpolated as executable Python. Standalone console script.
    template = Path(__file__).with_name('resolve_import_template.py').read_text(encoding='utf-8')
    return template + '\n\nimport base64\npayload = json.loads(base64.b64decode(' + repr(encoded) + '))\nrun_import(payload, globals().get("resolve"))\n'
