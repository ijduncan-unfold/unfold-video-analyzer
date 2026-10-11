"""Read source facts from ffprobe, never infer them with a language model."""
import asyncio
import json
from fractions import Fraction
from pathlib import Path
from app.utils.media_process import hidden_process_kwargs


async def _stop_process(proc):
    """Release source-file handles before upload cancellation can remove a file."""
    if proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
    await proc.wait()


def _packet_cadence(video: dict, packets: list[dict]) -> dict:
    """Confirm the declared nominal rate against every presentation timestamp.

    r_frame_rate alone is a guessed base rate, not proof of constant cadence:
    https://ffmpeg.org/doxygen/trunk/structAVStream.html
    Keep the independently reported average rate unchanged.
    """
    result = {
        "nominal_frame_rate_fraction": video.get("r_frame_rate"),
        "nominal_frame_rate_verified": False,
        "frame_rate_verification": "unverified",
        "video_time_base": video.get("time_base"),
    }
    try:
        rate = Fraction(video.get("r_frame_rate", "0/1"))
        time_base = Fraction(video.get("time_base", "0/1"))
        expected_count = int(video.get("nb_frames", 0))
        if rate <= 0 or time_base <= 0 or expected_count < 2 or len(packets) != expected_count:
            return result
        pts = sorted(int(packet["pts"]) for packet in packets)
        period_ticks = 1 / (rate * time_base)
        # Very coarse stream clocks cannot distinguish cadence reliably.
        if period_ticks < 10 or any(b <= a for a, b in zip(pts, pts[1:])):
            return result
        # Check total phase, not just consecutive steps: small per-frame drift
        # must not accumulate into a different editing timebase on long media.
        if any(abs((value - pts[0]) - index * period_ticks) > 1
               for index, value in enumerate(pts)):
            return result
        result.update(nominal_frame_rate_verified=True,
                      frame_rate_verification="ffprobe_full_packet_pts",
                      frame_rate_verified_frames=len(pts))
    except (TypeError, ValueError, ZeroDivisionError, KeyError):
        pass
    return result


async def _probe_packet_cadence(path: str, video: dict) -> dict:
    unverified = _packet_cadence(video, [])
    # Without an expected frame count the full packet scan cannot establish cadence.
    # MXF camera originals often omit nb_frames; do not read gigabytes pointlessly.
    try:
        if int(video.get('nb_frames', 0)) < 2:
            return unverified
    except (TypeError, ValueError):
        return unverified
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_packets",
        "-show_entries", "packet=pts", "-of", "json", path,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        **hidden_process_kwargs(),
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
    except asyncio.TimeoutError:
        await _stop_process(proc)
        return unverified
    except asyncio.CancelledError:
        await _stop_process(proc)
        raise
    if proc.returncode:
        return unverified
    try:
        return _packet_cadence(video, json.loads(stdout).get("packets", []))
    except (ValueError, TypeError):
        return unverified


async def probe_media(path: str) -> dict:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", path,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        **hidden_process_kwargs(),
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
    except asyncio.TimeoutError:
        await _stop_process(proc)
        raise ValueError("Media inspection timed out")
    except asyncio.CancelledError:
        await _stop_process(proc)
        raise
    if proc.returncode:
        raise ValueError("This file could not be read as video")
    data = json.loads(stdout)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ValueError("No video stream found in this file")
    fmt = data.get("format", {})
    rate = video.get("avg_frame_rate", "0/1")
    try:
        fps = float(Fraction(rate))
    except (ValueError, ZeroDivisionError):
        fps = 0
    if fps <= 0:
        # Camera MXF often omits avg_frame_rate while its stream declares a
        # usable nominal rate. This fallback does not certify packet cadence.
        try:
            nominal = video.get('r_frame_rate', '0/1')
            fps = float(Fraction(nominal))
            rate = nominal if fps > 0 else '0/1'
        except (ValueError, ZeroDivisionError):
            fps, rate = 0, '0/1'
    timing = await _probe_packet_cadence(path, video)
    return {
        "duration_seconds": float(fmt.get("duration", video.get("duration", 0))),
        "width": video.get("width", 0), "height": video.get("height", 0),
        "frame_rate": fps, "frame_rate_fraction": rate, "codec": video.get("codec_name", ""),
        "source_timecode": video.get("tags", {}).get("timecode") or fmt.get("tags", {}).get("timecode") or
            next((s.get("tags", {}).get("timecode") for s in streams if s.get("tags", {}).get("timecode")), None),
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
        "audio_stream_count": sum(s.get("codec_type") == "audio" for s in streams),
        "audio_channels": [s.get('channels', 0) for s in streams if s.get('codec_type') == 'audio'],
        "audio_sample_rates": [int(s.get('sample_rate') or 0) for s in streams if s.get('codec_type') == 'audio'],
        "pixel_format": video.get("pix_fmt"), "color_space": video.get("color_space"),
        "sample_aspect_ratio": video.get('sample_aspect_ratio'),
        "color_transfer": video.get('color_transfer'), "color_primaries": video.get('color_primaries'),
        "container": fmt.get('format_name'),
        "camera_manufacturer": fmt.get('tags', {}).get('company_name') or fmt.get('tags', {}).get('manufacturer'),
        "rotation": next((s.get("rotation") for s in video.get("side_data_list", []) if "rotation" in s), 0),
        **timing,
    }


async def create_poster(path: str, output: str, duration: float):
    from app.utils.display_geometry import POSTER_SCALE
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-v", "error", "-ss", str(min(1, max(0, duration / 3))), "-i", path,
        "-frames:v", "1", "-vf", POSTER_SCALE, "-q:v", "3", "-y", output,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        **hidden_process_kwargs(),
    )
    try:
        await asyncio.wait_for(proc.wait(), timeout=45)
    except asyncio.TimeoutError:
        await _stop_process(proc)
    except asyncio.CancelledError:
        await _stop_process(proc)
        raise
