"""Read-only Blackmagic RAW COM adapter, isolated from the web process.

Uses the documented Windows SDK interface (IUnknown/BSTR), not an editor API.
Native resources and callbacks remain alive until FlushJobs completes. Only
derived MP4/WAV/progress files are written; no SDK sidecar/trim/write methods.
"""
import argparse
import ctypes as C
from fractions import Fraction
import json
import os
from pathlib import Path
import subprocess
import threading
import uuid
import wave

import cv2
import numpy as np
from app.utils.media_process import hidden_process_kwargs

P, U, Q, H = C.c_void_p, C.c_uint32, C.c_uint64, C.c_int32
CALL = getattr(C, 'WINFUNCTYPE', C.CFUNCTYPE)
CONFIG = '267E9866-FB40-4BFB-8BF8-96EA3F7DA36E'
AUDIO = '76D4ACED-E0D6-45BB-B547-56B7435B2A1D'
CALLBACK = 'E9F98FAC-33DB-4A65-BB94-8A82B027AED0'
BGRA, CPU, PCM = 0x62677261, 0x63707562, 0x70636D6C


def check(result):
    if result < 0:
        raise ValueError(f'Blackmagic RAW SDK returned 0x{result & 0xffffffff:08x}')


def invoke(ptr, slot, types=(), args=(), restype=H):
    if not ptr:
        raise ValueError('Blackmagic RAW SDK returned a null interface')
    address = C.cast(ptr, C.POINTER(C.POINTER(P))).contents[slot]
    result = CALL(restype, P, *types)(address)(ptr, *args)
    if restype is H:
        check(result)
    return result


def release(ptr):
    if ptr:
        invoke(ptr, 2, restype=U)


def query(ptr, iid, optional=False):
    guid = C.create_string_buffer(uuid.UUID(iid).bytes_le)
    out = P()
    try:
        invoke(ptr, 0, (P, C.POINTER(P)), (guid, C.byref(out)))
    except ValueError:
        if optional:
            return None
        raise
    return out


def value(ptr, slot, kind=U):
    out = kind()
    invoke(ptr, slot, (C.POINTER(kind),), (C.byref(out),))
    return out.value


def rate_fraction(rate):
    # GetFrameRate returns float32. Recover common camera rational cadences
    # within float32 precision, rather than treating 23.976 as exactly 2997/125.
    for numerator in (24000, 30000, 60000, 120000):
        rate_exact = Fraction(numerator, 1001)
        if abs(float(rate_exact) - rate) < .00002:
            return rate_exact
    return Fraction(rate).limit_denominator(10000)


def write_state(path, state):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state), encoding='utf-8')
    temporary.replace(path)


class Decoder:
    def __init__(self, dll, shared):
        self.handles = [os.add_dll_directory(str(Path(dll).parent)), os.add_dll_directory(str(shared))]
        self.library = C.CDLL(str(dll), winmode=0x1500)
        factory_fn = self.library.CreateBlackmagicRawFactoryInstance
        factory_fn.argtypes, factory_fn.restype = [], P
        self.factory = factory_fn()
        self.codec, self.config, self.clip, self.audio = P(), None, P(), None
        self.callback = None
        invoke(self.factory, 3, (C.POINTER(P),), (C.byref(self.codec),))
        self.config = query(self.codec, CONFIG)
        invoke(self.config, 3, (U, P, P), (CPU, None, None))
        invoke(self.config, 6, (U,), (2,))
        self.ole = C.WinDLL('oleaut32')
        self.ole.SysAllocString.argtypes, self.ole.SysAllocString.restype = [C.c_wchar_p], P
        self.ole.SysFreeString.argtypes, self.ole.SysFreeString.restype = [P], None

    def text(self, ptr, slot, extra_types=(), extra_args=()):
        out = P()
        invoke(ptr, slot, (*extra_types, C.POINTER(P)), (*extra_args, C.byref(out)))
        try:
            return C.wstring_at(out) if out else ''
        finally:
            self.ole.SysFreeString(out)

    def open(self, path):
        name = self.ole.SysAllocString(str(path))
        try:
            invoke(self.codec, 3, (P, C.POINTER(P)), (name, C.byref(self.clip)))
        finally:
            self.ole.SysFreeString(name)
        self.audio = query(self.clip, AUDIO, optional=True)
        width, height = value(self.clip, 3), value(self.clip, 4)
        frames, rate = value(self.clip, 6, Q), rate_fraction(value(self.clip, 5, C.c_float))
        if not (0 < width * height <= 150_000_000 and 0 < frames <= 10_000_000 and 0 < rate <= 1000):
            raise ValueError('Invalid Blackmagic RAW dimensions or timing')
        channels = value(self.audio, 5) if self.audio else 0
        samples = value(self.audio, 7, Q) if channels else 0
        if channels and not (value(self.audio, 3) == PCM and value(self.audio, 4) in (16, 24, 32)
                             and channels <= 32 and 8000 <= value(self.audio, 6) <= 384000):
            raise ValueError('Unsupported Blackmagic RAW audio layout')
        self.facts = {'codec': 'braw', 'width': width, 'height': height, 'camera_frame_count': frames,
            'frame_rate': float(rate), 'frame_rate_fraction': str(rate), 'duration_seconds': frames / float(rate),
            'nominal_frame_rate_fraction': str(rate), 'nominal_frame_rate_verified': True,
            'has_audio': bool(samples), 'audio_stream_count': 1 if samples else 0,
            'source_timecode': self.text(self.clip, 7, (Q,), (0,)),
            'camera_metadata': {'camera_model': self.text(self.clip, 11),
                                'metadata_source': 'Blackmagic RAW SDK', 'decoder_version': self.text(self.config, 12)},
            'technical_basis': 'Blackmagic RAW SDK original dimensions, frame count, rate, timecode and audio'}
        return self.facts

    def close(self):
        if self.codec:
            invoke(self.codec, 8)
            if self.callback:
                invoke(self.codec, 5, (P,), (None,))
        for ptr in (self.audio, self.clip, self.config, self.codec, self.factory):
            release(ptr)
        for handle in self.handles:
            handle.close()

    def audio_wav(self, path):
        if not self.facts['has_audio']:
            return False
        channels, depth, rate = value(self.audio, 5), value(self.audio, 4), value(self.audio, 6)
        total = value(self.audio, 7, Q)
        stride, index = channels * (depth // 8), 0
        buffer = C.create_string_buffer(8192 * stride)
        with wave.open(str(path), 'wb') as out:
            out.setnchannels(channels)
            out.setsampwidth(depth // 8)
            out.setframerate(rate)
            while index < total:
                samples, size = U(), U()
                invoke(self.audio, 8, (C.c_int64, P, U, U, C.POINTER(U), C.POINTER(U)),
                    (index, buffer, len(buffer), min(8192, total - index), C.byref(samples), C.byref(size)))
                if not (0 < samples.value <= min(8192, total - index) and size.value == samples.value * stride):
                    raise ValueError('Blackmagic RAW audio read was incomplete')
                out.writeframesraw(buffer.raw[:size.value])
                index += samples.value
        return True


class Callback:
    def __init__(self, decoder, scale):
        self.decoder, self.scale = decoder, scale
        self.done, self.error, self.image = threading.Event(), None, None
        self.functions = []
        self.refs = 1

        def qi(this, iid, out):
            if C.string_at(iid, 16) in (uuid.UUID(CALLBACK).bytes_le, uuid.UUID('00000000-0000-0000-C000-000000000046').bytes_le):
                out[0] = self.ptr.value
                self.refs += 1
                return 0
            out[0] = None
            return C.c_int32(0x80004002).value

        def addref(this):
            self.refs += 1
            return self.refs

        def unref(this):
            self.refs -= 1
            return self.refs

        def read(this, job, result, frame):
            try:
                check(result)
                invoke(frame, 9, (U,), (self.scale,))
                invoke(frame, 11, (U,), (BGRA,))
                decoded = P()
                invoke(frame, 14, (P, P, C.POINTER(P)), (None, None, C.byref(decoded)))
                try:
                    invoke(decoded, 3)
                finally:
                    release(decoded)
            except BaseException as exc:
                self.error = str(exc)
                self.done.set()

        def processed(this, job, result, image):
            try:
                check(result)
                w, h, size = value(image, 3), value(image, 4), value(image, 8)
                if value(image, 7) != BGRA or not (0 < w * h <= 16_000_000 and size == w * h * 4):
                    raise ValueError('Unexpected Blackmagic RAW CPU image layout')
                ptr = value(image, 5, P)
                self.image = np.frombuffer(C.string_at(ptr, size), dtype=np.uint8).reshape(h, w, 4)
            except BaseException as exc:
                self.error = str(exc)
            finally:
                self.done.set()

        def failed(this, job, result, *args):
            if result < 0:
                self.error = f'Blackmagic RAW job failed: 0x{result & 0xffffffff:08x}'
                self.done.set()

        definitions = [(H, (P, P, C.POINTER(P)), qi), (U, (P,), addref), (U, (P,), unref),
            (None, (P, P, H, P), read), (None, (P, P, H), failed),
            (None, (P, P, H, P), processed), (None, (P, P, C.c_float), lambda *args: None),
            (None, (P, P, H), failed), (None, (P, P, P, U, P), lambda *args: None),
            (None, (P, P, P, U, P), lambda *args: None), (None, (P, P, H), failed)]
        for result, types, fn in definitions:
            self.functions.append(CALL(result, *types)(fn))
        self.vtable = (P * len(self.functions))(*(C.cast(fn, P).value for fn in self.functions))
        self.instance = (P * 1)(C.cast(self.vtable, P).value)
        self.ptr = C.cast(self.instance, P)
        decoder.callback = self
        invoke(decoder.codec, 5, (P,), (self.ptr,))

    def frame(self, index):
        self.done.clear()
        self.image, self.error = None, None
        job = P()
        invoke(self.decoder.clip, 18, (Q, C.POINTER(P)), (index, C.byref(job)))
        try:
            invoke(job, 3)
            if not self.done.wait(120):
                invoke(job, 4)
                raise ValueError('Blackmagic RAW frame decoding timed out')
            if self.error or self.image is None:
                raise ValueError(self.error or 'Blackmagic RAW frame was not decoded')
            return self.image
        finally:
            release(job)


def encode(decoder, args):
    target = Path(args.target)
    video, partial, audio = target.with_suffix('.video.mp4'), target.with_suffix('.partial.mp4'), target.with_suffix('.wav')
    facts = decoder.facts
    scale = 0x66756C6C
    for divisor, code in ((8, 0x65697468), (4, 0x71727472), (2, 0x68616C66)):
        if facts['width'] / divisor >= 1280:
            scale = code
            break
    callback = decoder if hasattr(decoder, 'frame') else Callback(decoder, scale)
    label = 'RED RAW' if facts['codec'] == 'r3d' else 'Blackmagic RAW'
    frame = cv2.cvtColor(callback.frame(0), cv2.COLOR_BGRA2BGR)
    h, w = frame.shape[:2]
    factor = min(1, 1280 / w, 720 / h)
    width, height = max(2, int(w * factor) // 2 * 2), max(2, int(h * factor) // 2 * 2)
    log = target.with_suffix('.ffmpeg.log')
    encoder = None
    try:
        with log.open('wb') as error:
            encoder = subprocess.Popen([args.ffmpeg, '-nostdin', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'bgr24',
                '-s', f'{width}x{height}', '-r', facts['frame_rate_fraction'], '-i', 'pipe:0', '-an', '-c:v', 'libx264',
                '-threads', '2', '-preset', 'veryfast', '-crf', '23', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-y', str(video)],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=error, **hidden_process_kwargs())
            for index in range(facts['camera_frame_count']):
                if index:
                    frame = cv2.cvtColor(callback.frame(index), cv2.COLOR_BGRA2BGR)
                if frame.shape[:2] != (h, w):
                    raise ValueError(f'{label} changed image dimensions during decoding')
                encoder.stdin.write(cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA).tobytes())
                if index % 12 == 0:
                    write_state(Path(args.progress), {'stage': 'preparing_proxy', 'message': f'Preparing {label} proxy',
                        'percent': round((index + 1) / facts['camera_frame_count'] * 95, 1), 'frames': index + 1, 'technical': facts})
            encoder.stdin.close()
            if encoder.wait(120):
                raise ValueError(f'{label} proxy encoder failed')
            if decoder.audio_wav(audio):
                mux = subprocess.run([args.ffmpeg, '-nostdin', '-v', 'error', '-i', str(video), '-i', str(audio),
                    '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
                    '-movflags', '+faststart', '-y', str(partial)], stderr=error, stdout=subprocess.DEVNULL,
                    timeout=600, **hidden_process_kwargs())
                if mux.returncode:
                    raise ValueError(f'{label} audio mux failed')
                partial.replace(target)
            else:
                video.replace(target)
    finally:
        if encoder and encoder.poll() is None:
            encoder.kill()
            encoder.wait()
        for file in (video, partial, audio, log):
            file.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser()
    for field in ('source', 'target', 'progress', 'dll', 'shared', 'ffmpeg'):
        parser.add_argument('--' + field, required=field != 'target' and field != 'ffmpeg')
    parser.add_argument('--probe', action='store_true')
    args = parser.parse_args(argv)
    progress, decoder = Path(args.progress), None
    try:
        decoder = Decoder(args.dll, args.shared)
        facts = decoder.open(Path(args.source))
        write_state(progress, {'stage': 'inspecting', 'message': 'Reading Blackmagic RAW metadata', 'technical': facts})
        if not args.probe:
            encode(decoder, args)
        write_state(progress, {'stage': 'complete', 'message': 'Blackmagic RAW ready', 'percent': 100,
                               'frames': facts['camera_frame_count'], 'technical': facts})
        return 0
    except Exception as exc:
        if args.target:
            Path(args.target).unlink(missing_ok=True)
        write_state(progress, {'stage': 'error', 'message': str(exc)})
        return 1
    finally:
        if decoder:
            decoder.close()


if __name__ == '__main__':
    raise SystemExit(main())
