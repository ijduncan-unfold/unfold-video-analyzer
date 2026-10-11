"""Keep original timing proof separate from user-supplied viewing proxies."""
import asyncio
from fractions import Fraction

from fastapi import HTTPException

CAMERA_METHODS = {
    'sony_f55_raw': 'sony_raw_full_decode',
    'blackmagic_raw': 'blackmagic_sdk_full_decode',
    'red_raw': 'red_sdk_full_decode',
}
VERIFIED_METHODS = frozenset(CAMERA_METHODS.values()) | {
    'ffprobe_full_packet_pts', 'arri_sdk_project_rate_and_count',
}


def verified_rate(technical):
    if (technical.get('nominal_frame_rate_verified') is True
            and technical.get('frame_rate_verification') in VERIFIED_METHODS):
        return technical.get('nominal_frame_rate_fraction')
    return None


def camera_timing_fields(technical, viewing):
    """Called only after complete native decoding and proxy checks succeed."""
    method = CAMERA_METHODS.get(viewing.get('decoder'))
    if not method or not viewing.get('generated') or viewing.get('linked'):
        return {}
    try:
        rate = Fraction(technical.get('frame_rate_fraction') or '0')
        count = technical.get('camera_frame_count')
        if (rate <= 0 or type(count) is not int or count < 1
                or technical.get('variable_frame_rate') or technical.get('is_vfr')
                or viewing.get('frame_count') != count
                or Fraction(viewing.get('frame_rate_fraction') or '0') != rate
                or abs(float(technical.get('duration_seconds') or 0) - count / float(rate)) > 1 / float(rate)
                or abs(float(viewing.get('duration_seconds') or 0) - count / float(rate)) > 1 / float(rate)):
            return {}
    except (ValueError, TypeError, ZeroDivisionError):
        return {}
    return {'nominal_frame_rate_verified': True, 'nominal_frame_rate_fraction': str(rate),
            'frame_rate_verified_frames': count, 'frame_rate_verification': method}


async def refresh_export_source(job):
    """Upgrade existing linked imports without reanalysis or decoding originals again.

    Only our successful full camera decode can establish RAW source timing.
    Recheck both file identities and every cached proxy PTS before upgrading.
    """
    from app.services.editor_bridge import ensure_source_unchanged
    from app.services.job_store import get_job, update_job
    from app.services.media_probe import probe_media

    technical = job.technical
    viewing = technical.get('viewing_proxy') or {}
    needs_timing = not verified_rate(technical) and bool(camera_timing_fields(technical, viewing))
    needs_audio = technical.get('has_audio') and not technical.get('audio_channels')
    if (job.media_origin != 'external' or job.status in ('queued', 'analyzing', 'processing', 'deleting')
            or not (needs_timing or needs_audio)):
        return job
    changes = {}
    try:
        await asyncio.to_thread(ensure_source_unchanged, job)
        if needs_timing and job.viewing_path:
            proxy = await probe_media(job.viewing_path)
            if (verified_rate(proxy) and proxy.get('frame_rate_verified_frames') == viewing.get('frame_count')
                    and proxy.get('frame_rate_fraction') == viewing.get('frame_rate_fraction')
                    and abs(proxy.get('duration_seconds', 0) - viewing.get('duration_seconds', 0)) < .01):
                changes.update(camera_timing_fields(technical, viewing))
        if needs_audio:
            original = await probe_media(job.local_path)
            if (original.get('has_audio') and (technical.get('audio_stream_count') is None
                    or original.get('audio_stream_count') == technical.get('audio_stream_count'))
                    and original.get('audio_channels')):
                changes.update(audio_channels=original['audio_channels'],
                               audio_stream_count=original['audio_stream_count'],
                               audio_sample_rates=original.get('audio_sample_rates', []))
        await asyncio.to_thread(ensure_source_unchanged, job)
    except (OSError, ValueError, HTTPException):
        return job
    latest = get_job(job.job_id)
    if (not latest or latest.status in ('queued', 'analyzing', 'processing', 'deleting')
            or latest.source_fingerprint != job.source_fingerprint or latest.viewing_fingerprint != job.viewing_fingerprint):
        return job
    return update_job(job.job_id, technical={**latest.technical, **changes}) if changes else latest
