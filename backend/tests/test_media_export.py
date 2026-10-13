"""Combined original-media handoffs, isolated fixtures; no provider requests."""
import copy
import io
import json
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import asdict

from app.services.job_store import create_job, update_job
from app.services.media_export import combined_project


def prepare(asset, tmp_path):
    return update_job(asset.job_id, local_path=str(tmp_path / 'original A.mov'),
        technical={**asset.technical, 'audio_channels': [2], 'audio_sample_rates': [48000]},
        analysis_config={'started_at': 'saved-run'},
        transcript=[{'start_time': '00:01', 'end_time': '00:02', 'text': 'Hello booth!'}],
        summary={'executive_summary': 'A visitor poses at the booth.'})


def second(job, tmp_path, audio=True):
    other = copy.deepcopy(job)
    other.job_id = 'second-media'
    other.filename = job.filename  # Identical filenames must not overwrite each other.
    other.local_path = str(tmp_path / 'another folder' / job.filename)
    other.technical.update(duration_seconds=12, frame_rate=30, frame_rate_fraction='30/1',
                           has_audio=audio, audio_channels=[1] if audio else [])
    return create_job(other)


def test_timeline_full_recordings_mixed_rates_linked_audio_and_all_metadata(asset, tmp_path):
    job = prepare(asset, tmp_path)
    other = second(job, tmp_path)
    before = [asdict(job), asdict(other)]
    root = ET.fromstring(combined_project([job, other], 'Comic Con'))
    masters = root.findall('project/children/bin/children/clip')
    assert len(masters) == 2
    seq = root.find('project/children/sequence')
    assert seq.findtext('duration') == '500'  # 20 seconds at first source's 25 fps.
    assert seq.findtext('rate/timebase') == '25'
    clips = seq.findall('media/video/track/clipitem')
    assert [(c.findtext('start'), c.findtext('end')) for c in clips] == [('0', '200'), ('200', '500')]
    assert [(c.findtext('in'), c.findtext('out')) for c in clips] == [('0', '200'), ('0', '360')]
    assert len(root.findall('.//file/pathurl')) == 2
    paths = [node.text for node in root.findall('.//file/pathurl')]
    assert any('original%20A.mov' in path for path in paths)
    assert any('another%20folder' in path for path in paths)
    identifiers = [node.get('id') for node in root.iter() if node.get('id') and node.tag != 'file']
    assert len(identifiers) == len(set(identifiers))
    full_files = {node.get('id') for node in root.findall('.//file') if node.find('pathurl') is not None}
    assert all(node.get('id') in full_files for node in root.findall('.//file'))
    assert len(seq.findall('media/audio/track')) == 2
    audio1 = seq.findall('media/audio/track')[0].findall('clipitem')
    assert len(audio1) == 2
    for clip in [*clips, *seq.findall('media/audio/track/clipitem')]:
        assert all(link.findtext('linkclipref') in identifiers for link in clip.findall('link'))
    assert 'Hello booth!' in ET.tostring(root, encoding='unicode')
    assert len(masters[0].findall('marker')) == 3
    note = masters[0].findtext('logginginfo/lognote')
    record = json.loads(note.split('Video Analyzer analysis:\n')[1])
    assert record['analysis']['flash_result']['total_shots'] == 2
    assert record['transcript'][0]['text'] == 'Hello booth!'
    assert 'private-provider' not in ET.tostring(root, encoding='unicode')
    assert [asdict(job), asdict(other)] == before


def test_bin_only_preserves_media_without_sequence(asset, tmp_path):
    job = prepare(asset, tmp_path)
    root = ET.fromstring(combined_project([job, second(job, tmp_path)], 'Booth library', 'bin'))
    assert root.find('.//sequence') is None
    assert len(root.findall('project/children/bin/children/clip')) == 2
    assert len(root.findall('.//file/pathurl')) == 2


def test_audio_link_indices_after_silent_source(asset, tmp_path):
    job = prepare(asset, tmp_path)
    silent = second(job, tmp_path, audio=False)
    root = ET.fromstring(combined_project([silent, job], 'Selection'))
    clip = root.findall('.//sequence/media/video/track/clipitem')[1]
    assert [link.findtext('clipindex') for link in clip.findall('link')] == ['2', '1', '1']


def test_routes_package_all_selected_and_reject_stale_or_missing(client, asset, tmp_path):
    job = prepare(asset, tmp_path)
    other = second(job, tmp_path)
    body = {'job_ids': [other.job_id, job.job_id]}
    options = client.post('/api/library/media-export-options', json=body)
    assert options.status_code == 200 and options.json()['available']
    assert [item['id'] for item in options.json()['items']] == body['job_ids']
    request = {**body, 'title': 'Comic Con', 'layout': 'timeline', 'include_metadata': True,
               'analysis_versions': {job.job_id: 'saved-run', other.job_id: 'saved-run'}}
    response = client.post('/api/library/media-export', json=request)
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert 'Video Analyzer.xml' in archive.namelist()
        assert len([name for name in archive.namelist() if name.endswith('.xmp')]) == 2
        assert len([name for name in archive.namelist() if name.endswith('.srt')]) == 2
        record = json.loads(archive.read('library.json'))
        assert [item['asset']['id'] for item in record['assets']] == body['job_ids']
        assert archive.read('clips.csv').decode('utf-8').count('Asset ID') == 1
        assert json.loads(archive.read('manifest.json'))['media_included'] is False
        assert not any(name.endswith('.mov') for name in archive.namelist())
    request['include_metadata'] = False
    assert ET.fromstring(client.post('/api/library/media-export', json=request).content).tag == 'xmeml'
    request['analysis_versions'][job.job_id] = 'old-run'
    assert client.post('/api/library/media-export', json=request).status_code == 409
    assert client.post('/api/library/media-export-options', json={'job_ids': [job.job_id, job.job_id]}).status_code == 422
    assert client.post('/api/library/media-export-options', json={'job_ids': ['missing']}).status_code == 404


def test_invalid_member_prevents_partial_xml_and_options_report_reason(client, asset, tmp_path):
    job = prepare(asset, tmp_path)
    other = second(job, tmp_path)
    update_job(other.job_id, technical={**other.technical, 'variable_frame_rate': True})
    ids = [job.job_id, other.job_id]
    options = client.post('/api/library/media-export-options', json={'job_ids': ids}).json()
    assert not options['available']
    assert options['items'][0]['available'] and 'variable frame rate' in options['items'][1]['reason']
    response = client.post('/api/library/media-export', json={'job_ids': ids,
        'analysis_versions': {id: 'saved-run' for id in ids}})
    assert response.status_code == 422


def test_combined_cosplay_exports_use_corrections_in_logging_markers_and_sidecars(client, asset, tmp_path):
    job = prepare(asset, tmp_path)
    flash = copy.deepcopy(job.flash_result)
    flash['scenes'][0]['shots'][0]['cosplay'] = [
        {'character': 'Iron Man', 'franchise': 'Marvel', 'identification': 'suggested'}]
    annotations = {'1': {'cosplay': [
        {'character': 'Gordon Freeman', 'franchise': 'Half-Life', 'costume_variant': 'HEV Suit',
         'visible_details': 'Orange armor', 'identification': 'confirmed'}]}}
    job = update_job(job.job_id, flash_result=flash, shot_annotations=annotations,
                     analysis_config={'mode': 'cosplay', 'started_at': 'saved-run'})
    other = second(job, tmp_path)
    for layout in ('timeline', 'bin'):
        response = client.post('/api/library/media-export', json={
            'job_ids': [job.job_id, other.job_id], 'layout': layout, 'include_metadata': True,
            'analysis_versions': {job.job_id: 'saved-run', other.job_id: 'saved-run'}})
        assert response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            root = ET.fromstring(archive.read('Video Analyzer.xml'))
            targets = root.findall('.//bin/children/clip') + root.findall('.//sequence/media/video/track/clipitem')
            assert len(targets) == (4 if layout == 'timeline' else 2)
            for clip in targets:
                description = clip.findtext('logginginfo/description')
                assert 'Gordon Freeman · Half-Life · HEV Suit [Confirmed by you]' in description
                assert 'Scene description:' in description and 'Iron Man' not in description
                assert 'Cosplay characters: Gordon Freeman' in clip.findtext('logginginfo/lognote')
                assert 'Gordon Freeman' in clip.findtext('marker/comment')
                assert 'Iron Man' not in clip.findtext('marker/comment')
            for name in archive.namelist():
                if not name.endswith('.xmp'):
                    continue
                sidecar = ET.fromstring(archive.read(name))
                ns = {'dc': 'http://purl.org/dc/elements/1.1/', 'rdf': 'http://www.w3.org/1999/02/22-rdf-syntax-ns#'}
                keywords = [node.text for node in sidecar.findall('.//dc:subject/rdf:Bag/rdf:li', ns)]
                assert 'Gordon Freeman' in keywords and 'Half-Life' in keywords
                assert 'Iron Man' not in keywords
            assert 'Gordon Freeman' in archive.read('clips.csv').decode('utf-8')
            record = json.loads(archive.read('library.json'))
            assert record['assets'][0]['shot_annotations']['1']['cosplay'][0]['character'] == 'Gordon Freeman'
    # An explicit removal also stays removed in the combined human-readable metadata.
    job = update_job(job.job_id, shot_annotations={'1': {'cosplay': []}})
    root = ET.fromstring(combined_project([job], 'Cleared costumes'))
    assert 'Gordon Freeman' not in root.findtext('.//logginginfo/description')
    assert 'Iron Man' not in root.findtext('.//marker/comment')
