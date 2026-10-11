# Camera originals and RAW workflows

Drop videos into the desktop window or use Import. The native drop/picker supplies the original absolute path, including Windows UNC paths. Originals are referenced in place, never copied into the library, changed or deleted. Keep the source share online, with its recording sidecars and file parts together.

A normal browser cannot reveal a dropped file's full local path. Browser Import opens the native desktop picker. The library import UI does not copy-upload camera originals.

## Connected decoder routes

| Recording | Windows import route | Real-media evidence / limits |
| --- | --- | --- |
| Sony F55 RAW MXF | Isolated, checksum-pinned Sony CPU worker; H.264/AAC viewing proxy | User's F55 recording: all 880 frames at 24000/1001 fps and four audio tracks. F5/F65 and X-OCN are not certified |
| Blackmagic BRAW | Isolated Blackmagic RAW CPU SDK worker; reduced-resolution development | Official URSA Mini Pro 12K clip: all 103 frames at 24 fps, source timecode and audio |
| RED R3D | Isolated RED SDK 9.2.1 worker through the app's read-only C ABI bridge | Real KOMODO-X 6K clip: all 275 frames at 24000/1001 fps, source timecode and audio |
| ARRIRAW ARI/ARX/MXF | ARRI Reference Tool CMD / Image SDK, Rec.709 development, then H.264/AAC | Twelve real ALEXA LF ARRIRAW frames and a RAW MXF containing those frames; 24 fps. HDE/ARRICORE variants remain unverified |
| Sony XAVC, Panasonic AVC-Intra/AVC-LongG, Canon XF-AVC, ProRes, MPEG-2, HEVC, DV | Actual FFmpeg probe and decoded-frame test, then complete H.264/AAC viewing proxy | Real DJI 4K ProRes recording and generated MPEG-2 MXF exercise this route. Individual camera/card workflows need validation |
| ProRes RAW | FFmpeg 8.1 native decoder, if the recording passes actual decoding | No successful ProRes RAW sample verification yet; recognizing the codec is not sufficient |
| Canon Cinema RAW / RAW Light CRM | Unsupported native path; an existing full-length viewing proxy can be linked through the editor API | Runtime DLL is present, but matching CRM video SDK declarations and a decoding fixture are still missing |

This is conditional routing, not a claim that every generation of these cameras works. Proprietary DLL load tests alone never enable a format. Sony, BRAW and RED workers run out of process; a native failure does not crash the library server. CPU development uses bounded buffers and two worker/encoder threads. Cancellation stops the app's own decoder process tree. No camera write, sidecar-save or trimming API is called.

## Original identity and viewing media

Import keeps original dimensions, codec, available source timecode, exact SDK cadence and camera metadata. Playback, thumbnails, Gemini and visual discovery use the cached viewing image. Editor handoff continues to reference the original. Source facts that cannot be established remain unknown; derived resolution is not substituted for original RAW resolution.

Native adapters check the complete proxy frame count, rational rate, duration and audio before publishing it. FFmpeg routes preserve timestamps and check duration, all audio streams and available original cadence. RED and BRAW preserve all native audio channels in an interleaved AAC track; Sony preserves its separate audio tracks. ARRI rejects a changed audio-stream layout when the original can be probed.

Complete Sony F55 MXFs can start with a nonzero picture counter. The Sony worker checks consecutive pictures relative to that first counter, while proxy playback still starts at the beginning of the clip. Gaps, repeated or reordered pictures, changing rasters, and incomplete frame counts remain errors; a nonzero first counter alone does not mean the recording is incomplete.

RED numbered file parts are canonicalized to the first part and checked for gaps. ARI/ARX sequences are canonicalized to their first numbered frame and checked for gaps. Companion files and sidecars are fingerprinted along with the original: changed, added, missing or offline members invalidate cached analysis. This does not certify arbitrary P2 card assembly or every editor's image-sequence relinking behavior.

Unchanged imports reuse the viewing proxy across restarts. Only derivatives and thumbnails are stored in the app-owned cache. Deleting a project removes those derivatives and keeps original media and any externally supplied viewing proxy. Auto/Always/Off control additional analysis proxy preparation; Off does not discard the viewing image needed to decode a RAW original.

Initial proxy preparation covers the complete recording and can take time for long films. Import reports progress while decoding. ARRI additionally creates a temporary reduced-resolution ProRes intermediate, which is removed after H.264 preparation. No full-size original copy is made.

## Viewing color

Sony F55 development uses stable sidecar RAW black/white levels with a Rec.709-like display curve. It does not apply a camera color matrix, calibrated white balance or creative LUT. Colors are approximate.

Blackmagic and RED use their SDK camera/sidecar development defaults and reduced-resolution 8-bit viewing output. ARRI uses the Image SDK's Rec.709/D65/BT.1886 transformation. FFmpeg uses its decoded image without automatically choosing a camera Log LUT. Color and match-cut results describe these viewing images. None of these proxies is a replacement for the original RAW grade.

## Portable resources

The existing repository contains the Windows x64 app/FFmpeg/runtime ZIP through Git LFS. The destination needs no separate Python, Node, Premiere or Resolve installation for the standalone tool. The native RED bridge is compiled and included; SDK developer headers, samples, compiler tools, test footage, keys and user databases are excluded.

ARRI Reference Tool's included EULA restricts copying/transfer. Its CMD package is therefore obtained directly from ARRI on the destination's first ARRI import, validated against pinned archive and binary SHA-256 hashes, and cached locally with its EULA. First use needs internet; later imports reuse the cache. An explicitly configured compatible decoder directory is also supported through `VIDEO_ANALYZER_DECODER_DIR`. Updated vendor binaries need verification before their pins change. Third-party terms remain separate from the app's source license; existing experimental runtime inclusion does not establish redistribution permission.

Windows binaries do not run on macOS. Apple Silicon decoder builds remain separate work.

To rebuild the read-only RED bridge, use an x64 Visual Studio Developer PowerShell with the separately obtained R3D SDK 9.2.1:

```powershell
./scripts/build-red-bridge.ps1 -Sdk C:/path/to/R3DSDK
```

The script statically links the CRT, so destination computers need no compiler. Pin the resulting bridge hash before packaging. `scripts/bundle-local-camera-decoders.py --red-runtime <redistributable-win-directory> --red-bridge <bridge.dll>` includes the matching runtime and supplied license. Never commit SDK developer headers or camera samples.

## Unsupported RAW fallback

The editor/local-media API can pair the original with an existing complete viewing proxy. Both are referenced in place. Confirm identical content, framing, first frame, duration, cadence and audio without trimming or retiming. Available original duration, rate, dimensions/aspect and timecode are checked; missing original facts are marked unverified. This advanced fallback is not a manual step in supported native drag-and-drop import.

Canon's public EOS EDSDK image API is not evidence of a CRM movie-decoding API. The publicly available Cinema RAW Development application can export viewing media, but an undocumented DLL ABI is not guessed or presented as a working native decoder.

## Validation

Sony was imported from the user's network share without changing its 4.36 GB original; its approximately 4.7 MB proxy contains all 880 frames and four audio streams. Blackmagic and RED fixtures came from official public camera samples. ARRI's sequence contains twelve actual sample frames, not a full feature recording. Tests distinguish real decoding from adapter mocks and generic DLL load checks.

Automated checks cover original preservation on deletion, cached reuse, changed sidecars/parts, missing sequence members, private path exclusion, original cadence, bounded native buffers, metadata parsing and safe package extraction. Frozen-app acceptance checks additionally exercise import progress, generated media playback and complete frame/audio verification. No paid Gemini request is needed to test decoding.

## Primary references

- [Blackmagic RAW and SDK](https://www.blackmagicdesign.com/products/blackmagicraw)
- [RED R3D SDK](https://www.reddigitalcinema.com/download/r3d-sdk)
- [ARRI Reference Tool CMD](https://www.arri.com/en/learn/camera-systems/tools/arri-reference-tool), [ARRI developer partner program](https://www.arri.com/en/company/the-arri-philosophy/arri-partner-program)
- [Canon Cinema RAW Development](https://app.ssw.imaging-saas.canon/app/es/crd.html), [Canon developer SDK programs](https://www.usa.canon.com/support/sdk)
- [Panasonic camera codecs](https://pro-av.panasonic.net/en/sales_o/p2/p2_camera_codec.pdf)
- [FFmpeg formats](https://ffmpeg.org/general.html), [FFmpeg 8.1 ProRes RAW decoder](https://ffmpeg.org/doxygen/8.1/prores__raw_8c.html)
- [Sony RAW Viewer guide](https://pro.sony/s3/2019/01/24031021/4461641181.pdf)
