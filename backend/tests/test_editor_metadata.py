import json
import xml.etree.ElementTree as ET

from app.services.editor_metadata import editor_metadata, map_fields
from app.services.job_store import Job
from app.services.library_export import export_library, NS


def footage():
    return Job(job_id='metadata-test', filename='camera.mxf', file_id='private', file_uri='private',
               mime_type='application/mxf', size_bytes=10, local_path='/originals/camera.mxf',
               technical={'duration_seconds': 10, 'source_timecode': '01:00:00:00'},
               metadata={'tags': ['campaign'], 'notes': 'Producer notes'},
               flash_result={'scenes': [{'scene_number': 99, 'scene_title': 'Processing section', 'shots': [
                   {'shot_number': 1, 'start_time': '00:00', 'end_time': '00:05',
                    'visual_description': 'A person in blue', 'tags': ['Blue'], 'subjects': ['person'],
                    'slates': [{'scene': '012B', 'take': '03', 'slate_shot': 'A', 'camera_roll': 'A001',
                                'production_title': 'Night film', 'cinematographer': 'J. Doe'}]},
                   {'shot_number': 2, 'start_time': '00:05', 'end_time': '00:10',
                    'visual_description': 'An empty room', 'tags': ['blue']}]}]},
               deep_results=[{'scene_number': 99, 'visual_analysis': {'lighting': 'Low key'}, 'api_key': 'secret'}],
               shot_annotations={'1': {'tags': ['approved'], 'notes': 'Editor note'}},
               transcript=[{'start_seconds': 4, 'end_seconds': 6, 'text': 'Meet me outside.', 'speaker': 'Alex'}],
               custom_result={'creative': 'An alternate view'}, rough_cut={'status': 'ready', 'clips': []})


def test_mapping_carries_slate_provenance_distinct_from_section_and_full_dialogue():
    job = footage()
    mapping = editor_metadata(job, include_record=True)
    fields = {item['key']: item for item in mapping['fields']}
    assert fields['scene']['value'] == '012B' and fields['scene']['origin'] == 'Slate reading · unreviewed'
    assert fields['camera_roll']['resolve'] == 'Roll/Card'
    assert 'Alex: Meet me outside.' in fields['transcript']['value']
    assert fields['keywords']['value'].lower().count('blue') == 1
    assert 'approved' in fields['keywords']['value']
    record = mapping['record']
    assert record['shots'][0]['notes'] == 'Editor note'
    assert record['shots'][0]['section_analysis']['visual_analysis']['lighting'] == 'Low key'
    assert record['shots'][1]['transcript_segments'][0]['text'] == 'Meet me outside.'
    assert record['analysis']['custom_result'] == job.custom_result
    assert record['analysis']['rough_cut'] == job.rough_cut
    assert record['technical']['source_timecode'] == '01:00:00:00'
    serialized = json.dumps(mapping)
    assert 'secret' not in serialized and '/originals/' not in serialized and 'file_uri' not in serialized
    assert 'thumbnail_url' not in serialized


def test_conflicting_slates_have_no_invented_clip_scene_and_keep_per_shot_readings():
    job = footage()
    job.flash_result['scenes'][0]['shots'][1]['slates'] = [{'scene': '013', 'take': '04'}]
    mapping = editor_metadata(job)
    assert not any(item['key'] in ('scene', 'take') for item in mapping['fields'])
    assert any('scene: multiple' in conflict for conflict in mapping['conflicts'])
    assert next(field['value'] for field in mapping['shot_fields']['1'] if field['key'] == 'scene') == '012B'
    assert next(field['value'] for field in mapping['shot_fields']['2'] if field['key'] == 'scene') == '013'
    assert 'record' not in mapping  # inspector/polling doesn't repeat the entire record
    assert not any(field['key'] == 'scene' for field in map_fields({}, [{'scene_number': 99}], [])['fields'])


def test_xmp_is_lossless_and_maps_production_fields_without_inventing_take_or_timecode():
    job = footage()
    root = ET.fromstring(export_library(job, 'xmp')[0])
    assert root.find('.//xmpDM:scene', NS).text == '012B'
    assert root.find('.//xmpDM:takeNumber', NS).text == '3'
    assert root.find('.//xmpDM:tapeName', NS).text == 'A001'
    assert root.find('.//xmpDM:directorPhotography', NS).text == 'J. Doe'
    assert 'Blue' in [item.text for item in root.findall('.//dc:subject/rdf:Bag/rdf:li', NS)]
    record = json.loads(root.find('.//va:analysisRecord', NS).text)
    assert record['transcript'] == job.transcript
    assert record['analysis']['deep_results'][0]['visual_analysis']['lighting'] == 'Low key'
    assert record['analysis']['rough_cut'] == job.rough_cut
    assert record['technical']['source_timecode'] == '01:00:00:00'
    job.flash_result['scenes'][0]['shots'][0]['slates'][0]['take'] = 'MOS'
    root = ET.fromstring(export_library(job, 'xmp')[0])
    assert root.find('.//xmpDM:takeNumber', NS) is None
    assert 'MOS' in root.find('.//va:editorMapping', NS).text
