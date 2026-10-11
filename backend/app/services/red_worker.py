"""Read-only RED SDK bridge in an isolated process; preserve full clip cadence."""
import argparse
import ctypes as C
from fractions import Fraction
from pathlib import Path
import wave

import numpy as np
from app.services.braw_worker import encode, write_state


class Info(C.Structure):
    _fields_ = [(key, C.c_uint64) for key in ('width', 'height', 'frames', 'numerator', 'denominator',
                'channels', 'samples', 'sample_rate')] + [('timecode', C.c_char * 128),
                ('camera', C.c_char * 256), ('sdk', C.c_char * 512)]


def aligned_buffer(size, alignment):
    if not 0 < size <= 64 * 1024 * 1024:
        raise ValueError('RED decode buffer exceeds the viewing limit')
    owner = C.create_string_buffer(size + alignment)
    ptr = (C.addressof(owner) + alignment - 1) // alignment * alignment
    return owner, ptr


class Decoder:
    def __init__(self, bridge, runtime):
        self.library = C.CDLL(str(bridge))
        self.handle, self.initialized = None, False
        definitions = {
            'va_red_initialize': (C.c_int, [C.c_char_p]), 'va_red_finalize': (None, []),
            'va_red_open': (C.c_void_p, [C.c_char_p]), 'va_red_close': (None, [C.c_void_p]),
            'va_red_info': (C.c_int, [C.c_void_p, C.POINTER(Info)]),
            'va_red_frame': (C.c_int, [C.c_void_p, C.c_uint64, C.c_void_p, C.c_size_t, C.c_uint]),
            'va_red_audio': (C.c_size_t, [C.c_void_p, C.c_uint64, C.c_size_t, C.c_void_p, C.c_size_t]),
        }
        for name, (result, types) in definitions.items():
            fn = getattr(self.library, name)
            fn.restype, fn.argtypes = result, types
        result = self.library.va_red_initialize(str(runtime).encode('utf-8'))
        self.initialized = True
        if result:
            self.close()
            raise ValueError(f'RED SDK initialization failed ({result})')

    def open(self, path):
        self.handle = self.library.va_red_open(str(path).encode('utf-8'))
        info = Info()
        if not self.handle or not self.library.va_red_info(self.handle, C.byref(info)):
            raise ValueError('RED SDK could not open the recording or its companion parts')
        if not (0 < info.width * info.height <= 150_000_000 and 0 < info.frames <= 10_000_000
                and info.numerator and info.denominator and info.channels <= 32):
            raise ValueError('Invalid RED source dimensions, timing or audio')
        fps = Fraction(info.numerator, info.denominator)
        if not 0 < fps <= 1000 or (info.samples and not 8000 <= info.sample_rate <= 384000):
            raise ValueError('Invalid RED recording cadence or audio rate')
        self.info, self.divisor = info, 1
        for divisor in (8, 4, 2):
            if info.width / divisor >= 1280 and info.width % (divisor * 4) == 0 and info.height % divisor == 0:
                self.divisor = divisor
                break
        self.width, self.height = info.width // self.divisor, info.height // self.divisor
        size = self.width * self.height * 4
        self.buffer, self.ptr = aligned_buffer(size, 16)
        self.size = size
        self.facts = {'codec': 'r3d', 'width': info.width, 'height': info.height,
            'camera_frame_count': info.frames, 'duration_seconds': info.frames / float(fps),
            'frame_rate': float(fps), 'frame_rate_fraction': str(fps),
            'nominal_frame_rate_fraction': str(fps), 'nominal_frame_rate_verified': True,
            'has_audio': bool(info.channels and info.samples), 'audio_stream_count': 1 if info.channels and info.samples else 0,
            'source_timecode': info.timecode.decode('utf-8', errors='replace'),
            'camera_metadata': {'manufacturer': 'RED', 'camera_model': info.camera.decode('utf-8', errors='replace'),
                'decoder_version': info.sdk.decode('utf-8', errors='replace').split(' [', 1)[0], 'metadata_source': 'RED SDK'},
            'technical_basis': 'RED SDK original resolution, exact rate numerator/denominator, frame count, timecode and audio'}
        return self.facts

    def frame(self, index):
        if not self.library.va_red_frame(self.handle, index, self.ptr, self.size, self.divisor):
            raise ValueError(f'RED frame {index + 1} could not be decoded')
        return np.frombuffer(C.string_at(self.ptr, self.size), dtype=np.uint8).reshape(self.height, self.width, 4)

    def audio_wav(self, path):
        if not self.facts['has_audio']:
            return False
        info, index = self.info, 0
        owner, ptr = aligned_buffer(8192 * info.channels * 4, 512)
        with wave.open(str(path), 'wb') as out:
            out.setnchannels(info.channels)
            out.setsampwidth(4)
            out.setframerate(info.sample_rate)
            while index < info.samples:
                count = min(8192, info.samples - index)
                read = self.library.va_red_audio(self.handle, index, count, ptr, count * info.channels * 4)
                if read != count:
                    raise ValueError('RED recording audio was incomplete')
                samples = np.frombuffer(C.string_at(ptr, count * info.channels * 4), dtype='>i4').astype('<i4')
                out.writeframesraw(samples.tobytes())
                index += count
        return True

    def close(self):
        if self.handle:
            self.library.va_red_close(self.handle)
            self.handle = None
        if self.initialized:
            self.library.va_red_finalize()
            self.initialized = False


def main(argv=None):
    parser = argparse.ArgumentParser()
    for field in ('source', 'target', 'progress', 'dll', 'shared', 'ffmpeg'):
        parser.add_argument('--' + field, required=field not in ('target', 'ffmpeg'))
    parser.add_argument('--probe', action='store_true')
    args = parser.parse_args(argv)
    progress, decoder = Path(args.progress), None
    try:
        decoder = Decoder(args.dll, Path(args.dll).parent)
        facts = decoder.open(Path(args.source))
        write_state(progress, {'stage': 'inspecting', 'message': 'Reading RED recording metadata', 'technical': facts})
        if not args.probe:
            encode(decoder, args)
        write_state(progress, {'stage': 'complete', 'message': 'RED RAW ready', 'percent': 100,
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
