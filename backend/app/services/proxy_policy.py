"""Choose analysis media without changing or relinking the original source."""
from pathlib import Path

from app.services.analysis_support import time_to_seconds

HEAVY_CODECS = {
    'prores', 'prores_raw', 'dnxhd', 'dnxhr', 'cfhd', 'cineform',
    'rawvideo', 'v210', 'v308', 'v408', 'r210', 'r10k',
    'ffv1', 'huffyuv', 'jpeg2000', 'braw', 'redcode', 'arriraw',
}
CAMERA_RAW_EXTENSIONS = {'.braw', '.r3d', '.ari', '.arx', '.crm', '.dng'}
HIGH_BITRATE = 50_000_000  # Estimated bytes-on-disk rate, including audio.


def proxy_decision(job, mode='auto'):
    if mode not in {'auto', 'always', 'off'}:
        raise ValueError('Proxy mode must be Auto, Always, or Off')
    decision = {'proxy_mode': mode, 'proxy_used': False, 'proxy_reason': '',
                'upload_strategy': 'youtube_url' if job.youtube_url else 'original'}
    if mode == 'off':
        decision['proxy_reason'] = 'Proxying is off; using the original source'
        return decision
    if job.youtube_url or not job.local_path:
        if mode == 'always':
            raise ValueError('Always proxy requires a local source video')
        decision['proxy_reason'] = 'No local source to proxy'
        return decision
    duration = time_to_seconds(job.technical.get('duration_seconds') or 0)
    codec = str(job.technical.get('codec') or '').lower()
    reason = ''
    if mode == 'always':
        reason = 'Always proxy selected'
    elif duration >= 1800:
        reason = 'Source is at least 30 minutes long'
    elif job.size_bytes > 1024 ** 3:
        reason = 'Source is larger than 1 GiB'
    elif codec in HEAVY_CODECS or Path(job.filename).suffix.lower() in CAMERA_RAW_EXTENSIONS:
        reason = f'Heavy camera format: {codec or Path(job.filename).suffix.lower()}'
    elif Path(job.filename).suffix.lower() in {'.mxf', '.mts', '.m2ts', '.mkv'}:
        reason = 'Camera/container format needs an analysis MP4'
    elif duration > 0 and job.size_bytes * 8 / duration >= HIGH_BITRATE:
        reason = 'Source data rate is at least 50 Mb/s'
    if reason:
        if duration <= 0:
            raise ValueError('Proxying requires a measured video duration; reimport the source or choose Off')
        decision.update(proxy_used=True, proxy_reason=reason, upload_strategy='segmented_proxy')
    else:
        decision['proxy_reason'] = 'Source does not need a proxy'
    return decision
