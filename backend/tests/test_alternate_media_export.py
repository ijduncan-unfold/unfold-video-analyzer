"""Real payloads and injected Resolve runtime; no external editor or model calls."""
import copy
import io
import json
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest
from app.services.job_store import update_job
from app.services.alternate_media_export import combined_fcpxml, combined_edl, resolve_payload, resolve_script
from app.services.media_export import export_checks
from app.services.nle_export import export_fcpxml
from app.services.resolve_import_template import run_import


def camera(asset, tmp_path, suffix='.braw'):
    path = tmp_path / ('Camera & clip' + suffix)
    path.write_bytes(b'camera fixture, not a decodable recording')
    return update_job(asset.job_id, filename=path.name, local_path=str(path), media_origin='upload',
        technical={**asset.technical, 'sample_aspect_ratio': '2:1', 'source_timecode': '01:00:00:00'},
        analysis_config={'started_at': 'run'})


def test_fcpxml_original_urls_anamorphic_timeline_and_single_export(asset, tmp_path):
    job = camera(asset, tmp_path)
    other = copy.deepcopy(job)
    other.job_id, other.filename = 'arri', 'ARRI.MXF'
    other.local_path = str(tmp_path / 'ARRI.MXF')
    other.technical = {**other.technical, 'frame_rate': 30, 'frame_rate_fraction': '30/1'}
    root = ET.fromstring(combined_fcpxml([job, other], 'RAW selects'))
    formats = root.findall('resources/format')
    assert len(formats) == 2 and all(node.attrib['paspH'] == '2' and node.attrib['paspV'] == '1' for node in formats)
    paths = [node.attrib['src'] for node in root.findall('.//media-rep')]
    assert paths[0].endswith('Camera%20%26%20clip.braw') and paths[1].endswith('ARRI.MXF')
    assert len(root.findall('.//event/asset-clip')) == 2
    assert len(root.findall('.//spine/asset-clip')) == 2
    assert root.find('.//spine/asset-clip').attrib['start'] == '3600/1s'
    assert root.find('.//sequence').attrib['duration'] == '16/1s'
    assert 'visual_description' in root.find('.//md[@key="org.videoanalyzer.analysis"]').attrib['value']
    assert root.find('.//event/asset-clip/note').text
    assert ET.fromstring(export_fcpxml(job)).find('.//format').attrib['paspH'] == '2'
    assert not export_checks([job])['available']  # legacy mapping is format specific
    assert export_checks([job], 'fcpxml')['available']


def test_event_and_resolve_ignore_timeline_facts_and_never_reference_proxy(asset, tmp_path):
    job = camera(asset, tmp_path, '.MXF')
    job.technical = {'nominal_frame_rate_verified': False, 'variable_frame_rate': True,
                     'sample_aspect_ratio': 'unknown', 'rotation': 90}
    job.viewing_path = str(tmp_path / 'proxy.mp4')
    assert not export_checks([job], 'fcpxml', 'timeline')['available']
    for format in ('fcpxml', 'resolve'):
        assert export_checks([job], format, 'bin')['available']
    root = ET.fromstring(combined_fcpxml([job], 'Originals', 'bin'))
    assert root.find('.//sequence') is None and root.find('.//format') is None
    assert root.find('.//asset').attrib == {'id': 'a1', 'name': job.filename}
    assert root.find('.//asset-clip').attrib == {'ref': 'a1', 'name': job.metadata.get('title') or job.filename}
    assert 'proxy.mp4' not in ET.tostring(root, encoding='unicode')
    payload = resolve_payload([job], 'Originals')
    assert payload['items'][0]['path'] == job.local_path
    assert payload['items'][0]['mapping']['record']['asset']['id'] == job.job_id


def test_edl_timecode_reel_identity_and_limits(asset, tmp_path):
    job = camera(asset, tmp_path)
    edl = combined_edl([job], 'Comic Con')
    assert '001  VA000001 V' in edl and '01:00:00:00 01:00:08:00 00:00:00:00 00:00:08:00' in edl
    assert job.local_path in edl and 'VIDEO ONLY' in edl
    other = copy.deepcopy(job)
    other.technical['frame_rate_fraction'] = '30/1'
    options = export_checks([job, other], 'edl')
    assert not options['available'] and 'one frame rate' in options['reason']
    with pytest.raises(ValueError, match='999'):
        combined_edl([job] * 1000, 'Too many')
    job.technical['source_timecode'] = None
    assert 'known embedded' in export_checks([job], 'edl')['items'][0]['reason']


def test_routes_format_specific_packages_and_extensions(client, asset, tmp_path):
    job = camera(asset, tmp_path)
    base = {'job_ids': [job.job_id], 'analysis_versions': {job.job_id: 'run'}}
    for format, filename, signature in [('fcpxml', 'Video Analyzer.fcpxml', b'<!DOCTYPE fcpxml>'),
                                       ('edl', 'Video Analyzer.edl', b'FCM: NON-DROP FRAME'),
                                       ('resolve', 'Import into Resolve.py', b'def run_import')]:
        response = client.post('/api/library/media-export', json={**base, 'format': format})
        assert response.status_code == 200, response.text
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert signature in archive.read(filename)
            assert 'Read me.txt' in archive.namelist() and 'library.json' in archive.namelist()
            assert json.loads(archive.read('manifest.json'))['media_count'] == 1
            if format == 'edl':
                assert b'VA000001' in archive.read('EDL reels.csv')
        response = client.post('/api/library/media-export', json={**base, 'format': format, 'include_metadata': False})
        assert response.status_code == 200 and signature in response.content
        assert filename.split('.')[-1] in response.headers['content-disposition']
    assert client.post('/api/library/media-export-options', json={'job_ids': [job.job_id], 'format': 'resolve', 'layout': 'bin'}).json()['available']
    Path(job.local_path).unlink()
    assert not client.post('/api/library/media-export-options', json={'job_ids': [job.job_id], 'format': 'resolve'}).json()['available']


class Clip:
    def __init__(self, path): self.path, self.meta = path, {'Description': 'Editor description', 'Keywords': 'Keep me'}
    def GetUniqueId(self): return self.path
    def GetClipProperty(self, key): return self.path if key == 'File Path' else None
    def GetMetadata(self): return dict(self.meta)
    def SetMetadata(self, fields): self.meta.update(fields); return True


class Folder:
    def __init__(self, name): self.name, self.clips, self.folders = name, [], []
    def GetName(self): return self.name
    def GetUniqueId(self): return self.name
    def GetClipList(self): return self.clips[:]
    def GetSubFolderList(self): return self.folders[:]


class Resolve:
    def __init__(self): self.root = Folder('Root'); self.folder = self.root; self.project = self; self.imports = 0
    def GetProjectManager(self): return self
    def GetCurrentProject(self): return self.project
    def GetUniqueId(self): return 'project'
    def GetMediaPool(self): return self
    def GetRootFolder(self): return self.root
    def GetCurrentFolder(self): return self.folder
    def SetCurrentFolder(self, folder): self.folder = folder; return True
    def AddSubFolder(self, parent, name): folder = Folder(name); parent.folders.append(folder); return folder
    def ImportMedia(self, paths):
        self.imports += 1
        clips = [Clip(path) for path in paths]; self.folder.clips.extend(clips); return clips


def test_resolve_standalone_import_retry_and_editor_preservation(asset, tmp_path):
    job = camera(asset, tmp_path)
    host = Resolve()
    # Execute the exact downloadable script, including its embedded data decoder.
    script = resolve_script([job], "User's RAW selects")
    scope = {'__name__': '__main__', 'resolve': host}
    exec(compile(script, 'Import into Resolve.py', 'exec'), scope)
    assert host.imports == 1 and host.folder == host.root
    clip = host.root.folders[0].clips[0]
    assert clip.path == job.local_path and clip.meta['Description'] == 'Editor description'
    assert 'Keep me' in clip.meta['Keywords'] and 'Complete analysis JSON' in clip.meta['Comments']
    before = dict(clip.meta)
    summary = run_import(resolve_payload([job], "User's RAW selects"), host)
    assert summary['reused'] == 1 and summary['synced'] == 1 and host.imports == 1
    assert clip.meta == before


def test_resolve_changed_source_and_failed_native_readback_stop(asset, tmp_path):
    job = camera(asset, tmp_path)
    payload = resolve_payload([job], 'RAW')
    host = Resolve()
    Path(job.local_path).write_bytes(b'changed')
    with pytest.raises(RuntimeError, match='changed since export'):
        run_import(payload, host)
    assert host.imports == 0 and not host.root.folders
    payload = resolve_payload([job], 'RAW')
    def wrong_import(paths):
        clip = Clip('/wrong/original.braw'); host.folder.clips.append(clip); return [clip]
    host.ImportMedia = wrong_import
    with pytest.raises(RuntimeError, match='different media'):
        run_import(payload, host)
    assert host.folder == host.root
    assert host.root.folders[0].clips[0].meta == {'Description': 'Editor description', 'Keywords': 'Keep me'}


def test_cosplay_corrections_reach_all_alternative_active_metadata(asset, tmp_path):
    job = camera(asset, tmp_path)
    flash = copy.deepcopy(job.flash_result)
    flash['scenes'][0]['shots'][0]['cosplay'] = [{'character': 'Supergirl', 'franchise': 'DC', 'identification': 'suggested'}]
    job = update_job(job.job_id, flash_result=flash, analysis_config={'mode': 'cosplay'},
                     shot_annotations={'1': {'cosplay': [{'character': 'Wonder Woman', 'franchise': 'DC', 'identification': 'confirmed'}]}})
    root = ET.fromstring(combined_fcpxml([job], 'Costumes', 'bin'))
    note = root.find('.//asset-clip/note').text
    assert 'Wonder Woman' in note and 'Supergirl' not in note
    assert 'Wonder Woman' in [node.get('value') for node in root.findall('.//keyword')]
    assert 'Wonder Woman' in combined_edl([job], 'Costumes')
    host = Resolve()
    run_import(resolve_payload([job], 'Costumes'), host)
    assert 'Wonder Woman' in host.root.folders[0].clips[0].meta['Keywords']
    assert 'Supergirl' not in host.root.folders[0].clips[0].meta['Keywords']


def test_resolve_failed_write_and_project_switch_never_claim_success(asset, tmp_path):
    job = camera(asset, tmp_path)
    payload = resolve_payload([job], 'RAW')
    for change_project in (False, True):
        host = Resolve()
        def import_media(paths):
            clip = Clip(paths[0]); host.folder.clips.append(clip)
            if change_project:
                host.project = None
            else:
                clip.SetMetadata = lambda fields: False
            return [clip]
        host.ImportMedia = import_media
        with pytest.raises(RuntimeError, match='project changed' if change_project else 'rejected metadata'):
            run_import(payload, host)
        assert 'Comments' not in host.root.folders[0].clips[0].meta
