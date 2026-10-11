// Video Analyzer's read-only C ABI bridge. Build against an independently
// supplied RED developer SDK; SDK headers/sample code are not part of this repo.
#include "R3DSDK.h"
#include <cstring>
#include <cstdint>
#include <new>

using namespace R3DSDK;
#define EXPORT extern "C" __declspec(dllexport)

struct Info {
    uint64_t width, height, frames, numerator, denominator, channels, samples, sample_rate;
    char timecode[128], camera[256], sdk[512];
};

EXPORT int va_red_initialize(const char* directory) {
    return static_cast<int>(InitializeSdk(directory, OPTION_RED_NONE));
}

EXPORT void va_red_finalize() { FinalizeSdk(); }

EXPORT void* va_red_open(const char* path) {
    try {
        Clip* clip = new Clip(path);
        if (clip->Status() == LSClipLoaded) return clip;
        delete clip;
    } catch (...) {}
    return nullptr;
}

EXPORT void va_red_close(void* handle) { delete static_cast<Clip*>(handle); }

EXPORT int va_red_info(void* handle, Info* output) {
    if (!handle || !output) return 0;
    try {
        auto clip = static_cast<Clip*>(handle);
        std::memset(output, 0, sizeof(*output));
        output->width = clip->Width();
        output->height = clip->Height();
        output->frames = clip->VideoFrameCount();
        output->numerator = clip->MetadataItemAsInt("framerate_numerator");
        output->denominator = clip->MetadataItemAsInt("framerate_denominator");
        output->channels = clip->AudioChannelCount();
        output->samples = clip->AudioSampleCount();
        output->sample_rate = clip->MetadataItemAsInt("samplerate");
        if (const char* tc = clip->Timecode(0)) strncpy_s(output->timecode, tc, _TRUNCATE);
        auto camera = clip->MetadataItemAsString("camera_model");
        strncpy_s(output->camera, camera.c_str(), _TRUNCATE);
        if (const char* sdk = GetSdkVersion()) strncpy_s(output->sdk, sdk, _TRUNCATE);
        return 1;
    } catch (...) { return 0; }
}

EXPORT int va_red_frame(void* handle, uint64_t frame, void* buffer, size_t size, unsigned divisor) {
    if (!handle || !buffer || (reinterpret_cast<uintptr_t>(buffer) % 16)) return 0;
    try {
        auto clip = static_cast<Clip*>(handle);
        if (frame >= clip->VideoFrameCount()) return 0;
        VideoDecodeJob job;
        job.Mode = divisor == 8 ? DECODE_EIGHT_RES_GOOD : divisor == 4 ? DECODE_QUARTER_RES_GOOD :
                   divisor == 2 ? DECODE_HALF_RES_GOOD : DECODE_FULL_RES_PREMIUM;
        job.PixelType = PixelType_8Bit_BGRA_Interleaved;
        job.OutputBuffer = buffer;
        job.OutputBufferSize = size;
        return clip->DecodeVideoFrame(static_cast<size_t>(frame), job) == DSDecodeOK;
    } catch (...) { return 0; }
}

EXPORT size_t va_red_audio(void* handle, uint64_t start, size_t samples, void* buffer, size_t bytes) {
    if (!handle || !buffer || (reinterpret_cast<uintptr_t>(buffer) % 512)) return 0;
    try {
        if (static_cast<Clip*>(handle)->DecodeAudio(start, &samples, buffer, bytes) == DSDecodeOK) return samples;
    } catch (...) {}
    return 0;
}
