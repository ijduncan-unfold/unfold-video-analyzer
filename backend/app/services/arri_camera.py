"""ARRI Reference Tool command-line adapter; original sources remain read-only."""
import asyncio
from fractions import Fraction
import json
from pathlib import Path
import re
import time

from app.config import settings
from app.services.media_probe import probe_media
from app.services.native_camera import prepare_ffmpeg_viewing, stop_worker
from app.services.arri_runtime import arri_runtime
from app.utils.media_process import hidden_process_kwargs

def metadata_sets(path):
    # Large recordings contain per-frame metadata. Read only the bounded static
    # prefix, not hundreds of megabytes into the web process's memory.
    with Path(path).open('r', encoding='utf-8') as stream:
        prefix = stream.read(512 * 1024)
    decoder, result = json.JSONDecoder(), {}
    for key in ('clipBasedMetadataSets', 'descriptiveMetadataSets'):
        match = re.search(r'"' + key + r'"\s*:\s*', prefix)
        if not match:
            raise ValueError('ARRI original metadata could not be read')
        array, _ = decoder.raw_decode(prefix, match.end())
        for entry in array:
            result[entry['metadataSetName']] = entry['metadataSetPayload']
    return result


async def run_art(executable, arguments, folder, on_progress=None):
    proc = await asyncio.create_subprocess_exec(str(executable), *arguments,
        '--logpath', str(folder / 'arri-native.log'), cwd=str(folder),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, **hidden_process_kwargs())
    wait, started = asyncio.create_task(proc.wait()), time.monotonic()
    try:
        while not wait.done():
            if time.monotonic() - started > 21600:
                raise ValueError('ARRI proxy preparation timed out')
            if on_progress:
                on_progress({'stage': 'preparing_proxy', 'message': 'Developing ARRI camera media',
                             'elapsed_seconds': round(time.monotonic() - started)})
            await asyncio.wait({wait}, timeout=1)
        if proc.returncode:
            raise ValueError('ARRI decoder could not process this recording; the original has been kept')
    finally:
        if proc.returncode is None:
            await stop_worker(proc)
        await wait


async def prepare_arri_viewing(original, folder, on_progress=None):
    folder = Path(folder).resolve()
    if folder.parent != Path(settings.upload_dir).resolve():
        raise ValueError('Camera proxy cache must belong to this app')
    executable = await arri_runtime(on_progress)
    folder.mkdir(parents=True, exist_ok=True)
    metadata, intermediate = folder / 'arri-original.json', folder / 'arri-viewing.mxf'
    target, published = folder / 'camera-viewing.mp4', False
    try:
        if on_progress:
            on_progress({'stage': 'inspecting', 'message': 'Reading ARRI original metadata'})
        await run_art(executable, ['export', '--input', str(original), '--output', str(metadata),
                                  '--skip-audio', '--skip-look'], folder)
        sets = metadata_sets(metadata)
        count = int(sets['Clip Info']['clipDuration'])
        rate = Fraction(sets['Project Rate']['timebase'])
        rectangle = sets['Image Size']['displayRect']
        if not (0 < count <= 10_000_000 and 0 < rate <= 1000 and
                0 < rectangle['width'] * rectangle['height'] <= 150_000_000):
            raise ValueError('Invalid ARRI source timing or dimensions')
        metadata.unlink(missing_ok=True)
        await run_art(executable, ['process', '--input', str(original), '--output', str(intermediate),
            '--video-codec', 'prores422lt', '--target-colorspace', 'Rec.709/D65/BT.1886',
            '--output-width', '1280', '--render-platform', 'cpu'], folder, on_progress)
        if not intermediate.is_file() or not intermediate.stat().st_size:
            raise ValueError('ARRI decoder produced no viewing video')
        derived = await probe_media(str(intermediate))
        if (derived.get('codec') != 'prores' or Fraction(derived.get('frame_rate_fraction') or '0') != rate or
                abs(derived.get('duration_seconds', 0) - count / float(rate)) > 1 / float(rate) + .005):
            raise ValueError('ARRI developed media failed its timing checks')
        try:
            original_facts = await probe_media(str(original))
        except ValueError:
            original_facts = {}
        if original_facts and original_facts.get('audio_stream_count') != derived.get('audio_stream_count'):
            raise ValueError('ARRI viewing development changed the original audio stream layout')
        technical = {'codec': sets['Clip Info'].get('videoCodec') or 'arriraw',
            'width': rectangle['width'], 'height': rectangle['height'], 'camera_frame_count': count,
            'duration_seconds': count / float(rate), 'source_timecode': original_facts.get('source_timecode'),
            'has_audio': derived['has_audio'], 'audio_stream_count': derived['audio_stream_count'],
            'frame_rate': float(rate), 'frame_rate_fraction': str(rate), 'nominal_frame_rate_fraction': str(rate),
            'nominal_frame_rate_verified': True, 'frame_rate_verified_frames': count,
            'frame_rate_verification': 'arri_sdk_project_rate_and_count',
            'camera_metadata': {'manufacturer': 'ARRI', 'camera_model': sets.get('Camera Device', {}).get('cameraModel'),
                'metadata_source': 'ARRI Reference Tool / Image SDK', 'recording': sets.get('Clip Info'),
                'lens': sets.get('Lens Device'), 'color': {'viewing_colorspace': 'Rec.709/D65/BT.1886'}},
            'technical_basis': 'ARRI SDK original metadata; frame count, project rate and developed duration checked'}
        target, facts = await prepare_ffmpeg_viewing(intermediate, folder, technical, on_progress)
        facts.update(decoder='arri_raw', viewing_transform='ARRI Image SDK Rec.709/D65/BT.1886 development; no creative look applied')
        technical['viewing_proxy'] = facts
        published = True
        return target, technical
    finally:
        for file in (metadata, intermediate, folder / 'arri-native.log'):
            file.unlink(missing_ok=True)
        if not published:
            target.unlink(missing_ok=True)
