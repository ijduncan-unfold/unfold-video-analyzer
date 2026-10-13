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
    assert fields['camera_roll']['resolve'] == 'Roll Card #'
    assert fields['transcript']['resolve'] == 'Dialog Notes'
    assert fields['cinematographer']['xmp'] == 'xmpDM:directorPhotography'
    assert fields['production_title']['xmp'] == 'xmpDM:projectName'
    assert 'Blue' in fields['keywords']['xmp_value']
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
    assert root.find('.//xmpDM:projectName', NS).text == 'Night film'
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


def cosplay_footage():
    job = footage()
    job.analysis_config = {'mode': 'cosplay'}
    job.technical.update(frame_rate=24, width=1280, height=720, has_audio=False)
    job.flash_result['scenes'][0]['shots'][0]['cosplay'] = [
        {'character': 'Iron Man', 'franchise': 'Marvel', 'identification': 'suggested'}]
    job.shot_annotations['1']['cosplay'] = [
        {'character': 'Gordon Freeman', 'franchise': 'Half-Life', 'costume_variant': 'HEV Suit',
         'visible_details': 'Orange armor, glasses & lambda logo', 'activity': 'Posing', 'identification': 'confirmed'},
        {'character': '', 'franchise': '', 'visible_details': 'Red armor',
         'possible_characters': ['Samus Aran'], 'identification': 'unknown'}]
    job.flash_result['scenes'][0]['shots'][1]['cosplay'] = [
        {'character': 'Batman', 'franchise': 'DC', 'identification': 'suggested'}]
    return job


def test_cosplay_leads_description_preserves_scene_and_uses_reviewed_identifications():
    job = cosplay_footage()
    mapping = editor_metadata(job, include_record=True)
    fields = {field['key']: field for field in mapping['fields']}
    description = fields['description']['value']
    assert 'Gordon Freeman · Half-Life · HEV Suit [Confirmed by you]' in description
    assert '[Suggested]' in description and '[Unknown]' in description
    assert 'Possible characters (unconfirmed): Samus Aran' in description
    assert 'A person in blue' in description and 'Producer notes' in description
    assert 'Iron Man' not in description
    assert fields['scene_description']['value'] == 'A person in blue\n\nAn empty room'
    assert {'Cosplay', 'Gordon Freeman', 'Half-Life', 'HEV Suit', 'Batman'}.issubset(fields['keywords']['xmp_value'])
    assert 'Samus Aran' not in fields['keywords']['xmp_value']
    assert fields['cosplay_characters']['value'] == 'Gordon Freeman · Batman'
    shot_fields = {field['key']: field['value'] for field in mapping['shot_fields']['1']}
    assert 'Batman' not in shot_fields['description']
    assert mapping['record']['shots'][0]['cosplay'][0]['character'] == 'Gordon Freeman'
    assert job.flash_result['scenes'][0]['shots'][0]['cosplay'][0]['character'] == 'Iron Man'
    job.analysis_config['mode'] = 'flash_only'
    fields = {field['key']: field['value'] for field in editor_metadata(job)['fields']}
    assert fields['description'] == 'Producer notes'  # General modes retain their existing description.
    assert 'Gordon Freeman' in fields['cosplay_description']


def test_cosplay_xmp_xml_and_fcpxml_match_inspector_and_honor_selection_and_clearing():
    job = cosplay_footage()
    job.metadata['tags'].append('armor, orange')
    root = ET.fromstring(export_library(job, 'xmp', [1])[0])
    description = root.findtext('.//dc:description/rdf:Alt/rdf:li', namespaces=NS)
    assert 'Gordon Freeman' in description and 'Batman' not in description
    assert 'Iron Man' not in description
    tags = [item.text for item in root.findall('.//dc:subject/rdf:Bag/rdf:li', NS)]
    assert 'armor, orange' in tags and 'Gordon Freeman' in tags and 'Samus Aran' not in tags
    assert root.findtext('.//va:cosplayCharacters', namespaces=NS) == 'Gordon Freeman'
    assert 'Gordon Freeman' in root.findtext('.//xmpDM:comment', namespaces=NS)
    shot = json.loads(root.findtext('.//va:shotMetadata', namespaces=NS))
    assert shot['cosplay'][0]['character'] == 'Gordon Freeman'
    xml = ET.fromstring(export_library(job, 'premiere_xml', [1])[0])
    assert 'Gordon Freeman' in xml.findtext('.//logginginfo/description')
    assert 'Iron Man' not in xml.findtext('.//logginginfo/description')
    log = json.loads(xml.findtext('.//logginginfo/lognote'))
    assert log['editor_metadata']['cosplay_characters'] == 'Gordon Freeman'
    fcpxml = ET.fromstring(export_library(job, 'fcpxml', [1])[0])
    assert 'Gordon Freeman' in fcpxml.findtext('.//asset-clip/note')
    assert 'Half-Life' in [node.get('value') for node in fcpxml.findall('.//asset-clip/keyword')]
    job.shot_annotations['1']['cosplay'] = []
    root = ET.fromstring(export_library(job, 'xmp', [1])[0])
    assert root.find('.//va:cosplayCharacters', NS) is None
    assert 'Iron Man' not in root.findtext('.//dc:description/rdf:Alt/rdf:li', namespaces=NS)
