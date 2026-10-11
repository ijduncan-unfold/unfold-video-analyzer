"""Isolated Windows F55 RAW development worker.

The Control6/Bayer layout was investigated against SonyRawDev 5.3 and the
experimental reference at https://github.com/Dec18studios/fx5-xocn-tools.
Only the tested DLL and F55 group are accepted. No native calls run in the API.
This creates an analytical viewing image, not calibrated camera mastering output.
"""
from __future__ import annotations

import argparse
import ctypes as c
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import time

SONY_DLL_SHA256 = 'cb8bf7e13fa2a10005967e6d6eb1fa85079643e833251879252b268a08a91981'
MAX_PICTURE_BYTES = 64 * 1024 * 1024
MAX_PIXELS = 16_000_000


class RawBuffer(c.Structure):
    _fields_ = [('data', c.c_void_p * 3), ('size', c.c_uint * 3), ('pad', c.c_uint)]


def pictures(stream, size):
    """Walk MXF KLV boundaries, skipping non-picture values without copying them.

    No fixture-specific offset/stride, byte-pattern resync, or trusting a BER
    length beyond EOF. Frame sequence and constant raster are checked by run().
    """
    packets = 0
    while stream.tell() < size:
        key = stream.read(16)
        raw = stream.read(1)
        if len(key) != 16 or not key.startswith(b'\x06\x0e\x2b\x34') or not raw:
            raise ValueError('Invalid or truncated MXF KLV header')
        first = raw[0]
        if first & 128:
            count = first & 127
            if not 1 <= count <= 8:
                raise ValueError('Invalid MXF BER length')
            encoded = stream.read(count)
            if len(encoded) != count:
                raise ValueError('Truncated MXF BER length')
            length = int.from_bytes(encoded, 'big')
        else:
            length = first
        start = stream.tell()
        if length > size - start:
            raise ValueError('Truncated MXF payload')
        packets += 1
        if packets > 10_000_000:
            raise ValueError('Too many MXF packets')
        prefix = stream.read(min(length, 16))
        # Sony picture essence is an MXF generic-container picture element.
        if key[12] == 0x15 and prefix[:3] == b'SBR':
            if not 16 <= length <= MAX_PICTURE_BYTES:
                raise ValueError('Unsupported Sony picture size')
            payload = prefix + stream.read(length - len(prefix))
            if len(payload) != length:
                raise ValueError('Truncated Sony picture')
            yield payload
        stream.seek(start + length)


def picture_header(payload):
    if len(payload) < 16 or payload[:3] != b'SBR':
        raise ValueError('Not a Sony RAW picture')
    sequence = struct.unpack_from('>I', payload, 4)[0]
    width, half_height = struct.unpack_from('>HH', payload, 8)
    height = half_height * 2
    if width < 2 or height < 2 or width % 2 or height % 2 or width * height > MAX_PIXELS:
        raise ValueError('Unsupported Sony RAW raster')
    return sequence, width, height


class Decoder:
    def __init__(self, dll, shared, first, active_width, active_height):
        import numpy as np
        if sys.platform != 'win32' or c.sizeof(c.c_void_p) != 8:
            raise ValueError('Sony F55 decoding requires Windows x64')
        with dll.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != SONY_DLL_SHA256:
                raise ValueError('This Sony decoder version has not been validated')
        self.dev, self.initialized, self.directories = None, False, []
        try:
            self.directories = [os.add_dll_directory(str(dll.parent)), os.add_dll_directory(str(shared))]
            self.library = c.CDLL(str(dll), winmode=0x1500)
            ptr = c.c_void_p
            def bind(name, result, arguments):
                fn = getattr(self.library, name)
                fn.restype, fn.argtypes = result, arguments
                return fn
            self.create = bind('?create@SonyRawDev@sony_raw_dev@@SAPEAV12@XZ', ptr, [])
            self.destroy = bind('?destroy@SonyRawDev@sony_raw_dev@@SAXPEAV12@@Z', None, [ptr])
            self.deinitialize = bind('?deinitialize@SonyRawDev@sony_raw_dev@@UEAAXXZ', None, [ptr])
            group_query = bind('?getBitstreamGroup@SonyRawDev@sony_raw_dev@@UEBAHPEBXIPEAW4BITSTREAM_GROUP@2@@Z', c.c_int, [ptr, ptr, c.c_uint, c.POINTER(c.c_int)])
            attribute = bind('?getAttribute@SonyRawDev@sony_raw_dev@@UEBAHPEBURawDevControl6@2@PEAURawDevAttribute@2@@Z', c.c_int, [ptr, ptr, ptr])
            initialize = bind('?initialize@SonyRawDev@sony_raw_dev@@UEAAHPEBURawDevControl6@2@@Z', c.c_int, [ptr, ptr])
            self.develop = bind('?develop@SonyRawDev@sony_raw_dev@@UEAAHPEBXIPEAURawDevBuffer@2@@Z', c.c_int, [ptr, ptr, c.c_uint, c.POINTER(RawBuffer)])
            self.dev = self.create()
            if not self.dev:
                raise ValueError('Sony decoder could not initialize')
            _, coded_width, coded_height = picture_header(first)
            self.width, self.height = coded_width, coded_height
            if (not 0 < active_width <= coded_width or not 0 < active_height <= coded_height
                    or active_width % 2 or active_height % 2
                    or coded_width - active_width > 64 or coded_height - active_height > 64):
                raise ValueError('Unverified Sony active picture geometry')
            group, frame = c.c_int(-1), c.create_string_buffer(first)
            if group_query(self.dev, frame, len(first), c.byref(group)) or group.value != 2:
                raise ValueError('This adapter supports the tested F55 RAW bitstream group only')
            control_bytes = bytearray(0x58)
            struct.pack_into('<7I', control_bytes, 0, 0x6c6c7663, group.value,
                             coded_width, coded_height, coded_width, coded_height, 0x100)
            struct.pack_into('<I', control_bytes, 0x3c, 2)
            control, attributes = c.create_string_buffer(bytes(control_bytes)), c.create_string_buffer(8192)
            if attribute(self.dev, control, attributes) or initialize(self.dev, control):
                raise ValueError('Sony decoder rejected the recording geometry')
            self.initialized = True
            # Reuse generous bounded native buffers throughout the clip.
            self.allocations = [c.create_string_buffer(coded_width * coded_height * 8) for _ in range(3)]
            self.output = RawBuffer()
            for index, plane in enumerate(self.allocations):
                self.output.data[index], self.output.size[index] = c.addressof(plane), len(plane)
            h, w = self.height, self.width
            self.r = np.frombuffer(self.allocations[0], dtype='<u2', count=w*h//4).reshape(h//2, w//2)
            self.g = np.frombuffer(self.allocations[1], dtype='<u2', count=w*h//2).reshape(h//2, w)
            self.b = np.frombuffer(self.allocations[2], dtype='<u2', count=w*h//4).reshape(h//2, w//2)
            self.mosaic = np.empty((h, w), dtype=np.uint16)
            # F55 development returns coded-raster plane strides even when an
            # active raster is requested. Read the full planes before removing
            # the symmetric sensor padding; never reshape them at active width.
            self.crop = ((coded_width-active_width)//2, (coded_height-active_height)//2,
                         active_width, active_height)
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.dev:
            if self.initialized:
                self.deinitialize(self.dev)
            self.destroy(self.dev)
            self.dev = None
        for directory in self.directories:
            directory.close()
        self.directories = []

    def decode(self, payload, size, lut):
        import cv2
        frame = c.create_string_buffer(payload)
        if self.develop(self.dev, frame, len(payload), c.byref(self.output)):
            raise ValueError('Sony RAW frame development failed')
        self.mosaic[0::2, 0::2] = self.r
        self.mosaic[0::2, 1::2] = self.g[:, 1::2]
        self.mosaic[1::2, 0::2] = self.g[:, 0::2]
        self.mosaic[1::2, 1::2] = self.b
        bgr = cv2.cvtColor(self.mosaic, cv2.COLOR_BayerRG2RGB)
        x, y, w, h = self.crop
        bgr = bgr[y:y+h, x:x+w]
        bgr = cv2.resize(bgr, size, interpolation=cv2.INTER_AREA)
        return lut[bgr].tobytes()


def viewing_lut(black, white):
    """A stable clip-wide analytical display transform; never per-frame auto levels."""
    import numpy as np
    if not 0 <= black < white <= 65535:
        raise ValueError('Invalid Sony RAW viewing levels')
    linear = np.clip((np.arange(65536, dtype=np.float32) - black) / (white - black), 0, 1)
    display = np.where(linear < .018, 4.5 * linear, 1.099 * linear ** .45 - .099)
    return np.clip(display * 255 + .5, 0, 255).astype(np.uint8)


def emit(path, **state):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state), encoding='utf-8')
    temporary.replace(path)


def run(args):
    import cv2
    cv2.setNumThreads(2)
    source, target, progress = Path(args.source).resolve(strict=True), Path(args.target).resolve(), Path(args.progress).resolve()
    if target == source or target.suffix != '.mp4' or target.exists():
        raise ValueError('Choose a new app-owned MP4 output')
    if target.parent != progress.parent:
        raise ValueError('Worker outputs must share an app-owned folder')
    fps = Fraction(args.fps)
    if fps <= 0 or fps > 120 or args.frames < 1 or args.frames > 1_000_000:
        raise ValueError('Unsupported RAW recording timing')
    video = target.with_name(target.stem + '.video.mp4')
    partial = target.with_name(target.stem + '.partial.mp4')
    log_path = target.with_name(target.stem + '.ffmpeg.log')
    lut, count, decoder = viewing_lut(args.black, args.white), 0, None
    size = (1280, max(2, round(args.height / args.width * 1280 / 2) * 2))
    proc = None
    try:
        with source.open('rb') as stream, log_path.open('wb') as log:
            iterator = pictures(stream, source.stat().st_size)
            first = next(iterator, None)
            if first is None:
                raise ValueError('No Sony RAW picture essence was found')
            first_sequence, coded_width, coded_height = picture_header(first)
            decoder = Decoder(Path(args.dll).resolve(strict=True), Path(args.shared).resolve(strict=True), first, args.width, args.height)
            emit(progress, stage='decoding', message='Preparing Sony RAW proxy', percent=0, frames=0, total_frames=args.frames)
            proc = subprocess.Popen([args.ffmpeg, '-nostdin', '-v', 'error', '-f', 'rawvideo', '-pixel_format', 'bgr24',
                '-video_size', f'{size[0]}x{size[1]}', '-framerate', str(fps), '-i', 'pipe:0',
                '-an', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-threads', '2',
                '-pix_fmt', 'yuv420p', '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709',
                '-movflags', '+faststart', '-y', str(video)], stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
            payload = first
            last_update = 0
            while payload is not None:
                sequence, w, h = picture_header(payload)
                # Sony picture counters can begin above zero in a complete MXF.
                # They establish ordering, not the proxy's source-relative start.
                if sequence != first_sequence + count or (w, h) != (coded_width, coded_height) or count >= args.frames:
                    raise ValueError('Sony RAW frame sequence or raster changed')
                proc.stdin.write(decoder.decode(payload, size, lut))
                count += 1
                if time.monotonic() - last_update >= .5:
                    emit(progress, stage='decoding', message='Preparing Sony RAW proxy', percent=round(count / args.frames * 90),
                         frames=count, total_frames=args.frames)
                    last_update = time.monotonic()
                payload = next(iterator, None)
            proc.stdin.close()
            if proc.wait(timeout=120) or count != args.frames:
                raise ValueError('The Sony RAW proxy is incomplete')
            decoder.close()
            decoder = None
            emit(progress, stage='audio', message='Adding original audio to the proxy', percent=92, frames=count, total_frames=args.frames)
            duration = float(Fraction(count, 1) / fps)
            proc = subprocess.Popen([args.ffmpeg, '-nostdin', '-v', 'error', '-i', str(video), '-i', str(source),
                '-map', '0:v:0', '-map', '1:a?', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '128k',
                '-t', str(duration), '-map_metadata', '-1', '-movflags', '+faststart', '-y', str(partial)],
                stdout=subprocess.DEVNULL, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
            if proc.wait(timeout=max(300, duration * 5)):
                raise ValueError('Sony RAW audio could not be included in the proxy')
            partial.replace(target)
            emit(progress, stage='complete', message='Sony RAW proxy prepared', percent=100, frames=count, total_frames=count,
                 frame_rate_fraction=str(fps), width=size[0], height=size[1], color_calibrated=False)
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait()
        if decoder is not None:
            decoder.close()
        video.unlink(missing_ok=True)
        partial.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'target', 'progress', 'dll', 'shared', 'ffmpeg', 'fps'):
        parser.add_argument('--' + name, required=True)
    for name in ('width', 'height', 'frames'):
        parser.add_argument('--' + name, required=True, type=int)
    parser.add_argument('--black', type=float, default=512)
    parser.add_argument('--white', type=float, default=5472)
    args = parser.parse_args(argv)
    try:
        run(args)
        return 0
    except Exception as error:
        emit(Path(args.progress), stage='error', message=str(error) if isinstance(error, ValueError) else
             'Sony RAW decoder worker failed', error_type=type(error).__name__)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
