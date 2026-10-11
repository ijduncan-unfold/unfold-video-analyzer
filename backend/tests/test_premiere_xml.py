"""Editorial frame coordinates, linked audio and original-source references."""
import copy
import json
import xml.etree.ElementTree as ET
from fractions import Fraction

import pytest

from app.services.library_export import export_library
from app.services.premiere_xml import source_url
from test_selected_exports import export_asset  # reusable isolated editorial fixture


@pytest.mark.parametrize('rate,nominal,in_frame,out_frame', [
    ('25/1', 25, 250, 300), ('24000/1001', 24, 240, 288), ('30000/1001', 30, 300, 360)])
def test_selected_xml_links_original_video_and_all_audio(export_asset, rate, nominal, in_frame, out_frame):
    job = copy.deepcopy(export_asset)
    job.local_path = r'\\server\Footage folder\A & B.mxf'
    job.viewing_path = 'PRIVATE-PROXY.mp4'
    job.technical.update(frame_rate_fraction=rate, audio_channels=[1, 1, 2], audio_sample_rates=[48000] * 3,
        nominal_frame_rate_verified=True, nominal_frame_rate_fraction=rate,
        frame_rate_verification='sony_raw_full_decode')
    job.metadata['api_key'] = 'PRIVATE-KEY'
    payload, mime, extension = export_library(job, 'premiere_xml', [3])
    assert (mime, extension) == ('application/xml', 'xml')
    assert 'PRIVATE-' not in payload and 'UNRELATED_' not in payload
    root = ET.fromstring(payload)
    assert root.tag == 'xmeml' and root.get('version') == '5'
    assert root.findtext('sequence/rate/timebase') == str(nominal)
    assert root.findtext('sequence/rate/ntsc') == ('FALSE' if rate == '25/1' else 'TRUE')
    assert root.findtext('sequence/duration') == str(out_frame - in_frame)
    items = root.findall('.//clipitem')
    assert len(items) == 5 and len(root.findall('sequence/media/audio/track')) == 4
    full_file = root.find('.//file[name]')
    assert full_file.findtext('pathurl') == 'file://localhost//server/Footage%20folder/A%20%26%20B.mxf'
    assert full_file.findtext('timecode/frame') == str(3600 * nominal)
    assert full_file.findtext('media/audio/channelcount') == '4'
    assert full_file.findtext('media/audio/samplecharacteristics/samplerate') == '48000'
    for index, item in enumerate(items):
        assert item.findtext('in') == str(in_frame)  # do not add the 01:00 source TC twice
        assert item.findtext('out') == str(out_frame)
        assert item.findtext('start') == '0'
        assert item.findtext('end') == str(out_frame - in_frame)
        assert item.findtext('sourcetrack/trackindex') == str(max(1, index))
        assert {link.findtext('linkclipref') for link in item.findall('link')} == {node.get('id') for node in items}
    log = json.loads(items[0].findtext('logginginfo/lognote'))
    assert log['shot']['tags'] == ['circle'] and log['annotation']['notes'] == 'Keep'
    assert log['transcript'][0]['text'] == 'Circle words.'
    assert log['section_analysis'][0]['visual_analysis']['composition'] == 'Circle'


def test_file_zero_and_sequential_selected_clips(export_asset):
    job = copy.deepcopy(export_asset)
    job.technical.update(source_timecode=None, has_audio=False)
    payload = export_library(job, 'premiere_xml', [3, 4])[0]
    root = ET.fromstring(payload)
    assert 'not an asserted camera timecode' in payload
    assert root.find('.//audio') is None
    assert root.findtext('.//file/timecode/frame') == '0'
    assert [(clip.findtext('start'), clip.findtext('end')) for clip in root.findall('.//clipitem')] == [('0', '50'), ('50', '125')]
    assert len(root.findall('.//file[pathurl]')) == 0  # offline import can relink by source filename


@pytest.mark.parametrize('changes,message', [
    ({'audio_channels': []}, 'audio channel layout'), ({'rotation': 90}, 'orientation'),
    ({'sample_aspect_ratio': '2:1'}, 'pixel aspect'), ({'variable_frame_rate': True}, 'variable'),
    ({'source_timecode': '01:00:00;00'}, 'drop-frame'),
    ({'nominal_frame_rate_verified': False}, 'verified constant')])
def test_unknown_or_unsupported_timing_is_not_guessed(export_asset, changes, message):
    job = copy.deepcopy(export_asset)
    job.technical.update({'audio_channels': [2], **changes})
    with pytest.raises(ValueError, match=message):
        export_library(job, 'premiere_xml')


@pytest.mark.parametrize('path,url', [(r'C:\Camera files\take.mov', 'file://localhost/C:/Camera%20files/take.mov'),
    ('/Volumes/Camera/take.mov', 'file://localhost/Volumes/Camera/take.mov'), ('take.mov', None)])
def test_original_source_url(path, url):
    assert source_url(path) == url


@pytest.mark.parametrize('rate,source_tc,record_tc', [('24000/1001', '01:00:10:00', '00:00:02:00'),
    ('30000/1001', '01:00:10:00', '00:00:02:00')])
def test_fractional_edl_quantizes_at_exact_rate_and_formats_nominal_ndf(export_asset, rate, source_tc, record_tc):
    job = copy.deepcopy(export_asset)
    job.technical['frame_rate_fraction'] = rate
    text = export_library(job, 'edl', [3])[0]
    assert f'{source_tc} 01:00:12:00 00:00:00:00 {record_tc}' in text
    assert f'* SOURCE FRAME RATE: {Fraction(rate)}' in text


def test_premiere_xml_download_extension(client, export_asset):
    from app.services.job_store import update_job
    update_job(export_asset.job_id, technical={**export_asset.technical, 'audio_channels': [2]})
    response = client.get(f'/api/library/{export_asset.job_id}/export?format=premiere_xml&shot_numbers=3')
    assert response.status_code == 200 and '.xml' in response.headers['content-disposition']
    assert ET.fromstring(response.text).findtext('.//clipitem/in') == '250'
