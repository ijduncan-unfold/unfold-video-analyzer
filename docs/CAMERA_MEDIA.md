# Camera originals and RAW workflows

**Drop videos into the desktop window or use Import to choose files.** Native file drops and the file picker supply the original absolute path, including Windows UNC/network-share paths, automatically. There is no path-paste form. The app reads the source in place without copying the camera original into its library. The file/share must remain online.

Normal browser tabs hide absolute paths for dropped files. Browser Import opens the native desktop window and its picker; a browser drop opens that window so you can drop the original there. Source checkouts require `requirements-desktop.txt`; packaged desktop builds include the native window runtime. The existing copy-upload API remains for compatibility, but the library import UI does not use it.

The extension identifies a container, not its decoder. Import probes the actual video stream and tests one decoded frame. MXF is now recognized consistently by the picker, API, editor bridge, and proxy policy. Supported codecs depend on the installed FFmpeg build and the particular recording.

| Camera family | Direct proxy path | Proprietary RAW fallback |
| --- | --- | --- |
| Sony | Decodable XAVC/H.264, HEVC, MPEG-2 and other MXF/MOV codecs | F55/F5/F65 RAW and X-OCN: full-length viewing copy from Resolve or Sony RAW Viewer |
| Panasonic | Decodable AVC-Intra/H.264, HEVC, MPEG-2, ProRes and other MXF/MOV codecs | Use the appropriate vendor/editor decoder and link its full-length viewing copy |
| Canon | Decodable XF-AVC/H.264, HEVC and other MXF/MOV codecs | Cinema RAW/RAW Light CRM: full-length viewing copy from Resolve or Canon's RAW tools |
| ARRI | ProRes MOV and other codecs the installed decoder can read | ARRIRAW ARI/ARX/MXF: full-length viewing copy from Resolve or ARRI tools |
| Blackmagic | ProRes MOV | BRAW: vendor SDK or Resolve viewing copy; runtime included in the experimental portable collection; adapter not enabled |
| RED | Decodable editorial formats | R3D: REDline/REDCINE-X or Resolve viewing copy; runtime included in the experimental portable collection; adapter not enabled |
| ProRes RAW | Recent FFmpeg builds contain a decoder; import tests whether this recording actually decodes | Link an externally decoded viewing copy if the recording/build is unsupported |

This matrix describes routing, not certification of every camera generation or codec variant. The portable Windows collection includes experimental vendor runtime files, but native RAW adapters are not yet enabled in the app. Recognizing an extension does not install a decoder. DNG/DPX/EXR image sequences, spanned-clip stitching, multicam, automatic LUT selection and a color-managed RAW development pipeline are not implemented.

## Native vendor decoder integration plan

Broad native RAW support is feasible, but each family requires an actual decoder integration. Installing an editor's codec plug-in does not automatically make that decoder available to FFmpeg or this app. SDK availability also does not establish permission to redistribute its binaries in an open-source installer. These are planned integrations, not capabilities implemented in this revision.

| Format | Verified vendor route | Next integration step |
| --- | --- | --- |
| Sony RAW / X-OCN | [Sony RAW SDK licensing program](https://pro.sony/s3/2023/09/07140729/Sony_X-OCN_WhitePaper_v2.0.pdf), including SDK and technical documents | Obtain SDK access and distribution terms; validate F55 RAW first, then X-OCN generations |
| Blackmagic RAW | [Public, free SDK](https://www.blackmagicdesign.com/products/blackmagicraw) for Windows, macOS and Linux, with CPU/GPU decoding and metadata | Build a native helper against the SDK, review its redistribution agreement, then test Windows x64 and Apple Silicon |
| RED R3D | [Official R3D SDK download](https://www.reddigitalcinema.com/download/r3d-sdk); REDline offers a separate transcode route | Review the SDK license and implement its frame/audio/metadata API; validate spanned clips and current camera generations |
| ARRIRAW / ARRICORE / HDE | [ARRI Image SDK through its partner program](https://www.arri.com/en/company/the-arri-philosophy/arri-partner-program); [ART GUI/CMD](https://www.arri.com/en/learn/camera-systems/tools/arri-reference-tool) offers a separate transcode route | Obtain partner SDK access and terms; test both single-file MXF and sequence workflows |
| Canon Cinema RAW / RAW Light | [Cinema RAW Development](https://www.canon.com.au/apps/cinema-raw-development) or an approved editor can develop and export clips | Confirm access to a Cinema RAW video decoder SDK with Canon; the public [EDSDK RAW image-processing API](https://developercommunity.usa.canon.com/s/article/What-is-EDSDK-with-RAW-Processing-support) is not evidence of a CRM video decoder |
| Panasonic camera media | [P2 recording formats](https://pro-av.panasonic.net/en/sales_o/p2/p2_camera_codec.pdf) include non-RAW codecs handled by a compatible FFmpeg build | Route by actual recording codec; externally recorded BRAW and ProRes RAW need their respective decoder paths |

The intended pipeline is: inspect recording format and installed decoder; decode the original read-only with the appropriate helper; apply an explicit viewing transform; create a smaller analysis/playback proxy in the app-owned cache; retain frame-rate, source-timecode and original identity for edits. Store decoder/version, viewing transform and alignment provenance alongside analysis. Helpers must run out of process with cancellation, bounded resources, progress reporting and atomic proxy publication. Their presence alone must never be shown as proof that a particular clip decodes.

Where bundling is allowed, installers can carry the runtime with its notices. Otherwise use a separately installed vendor component or a full-length editor-generated viewing proxy. Sony F55 RAW is the first native target because the existing network-share recording provides a real validation fixture; BRAW is a practical next target because its documented SDK is public. This prioritization is an engineering judgment, not a claim that the required SDKs have already been obtained.

### Local decoder investigation (Windows, October 2026)

The installed Sony runtime was loaded directly from its existing application directory in an isolated Python process. It created a decoder, recognized the F55 bitstream, initialized, and decoded the first, middle and last pictures of the network-share F55 fixture. Premiere and Media Encoder were not called. Only those picture payloads were read into memory; no full-size original or decoder binary was copied. Diagnostic previews are not color calibrated. These experiments do not establish SDK permission, portable compatibility, or production support: the native pipeline still needs verified ABI/layouts, active-image cropping, viewing transforms, timing, audio and full-clip validation.

`scripts/inspect-camera-decoders.py` inventories existing Windows decoder installations, hashes their binaries, records PE versions, exports, direct/delayed dependencies and companion resources, and optionally tests DLL loading in separate processes. It does not copy binaries, invoke undocumented decoder methods, inspect originals, start editors or enable formats in the app. It requires `pefile`, which is included by the Windows desktop-build environment through PyInstaller.

Run from the repository with the desktop-build Python environment:

```powershell
.\.venv\Scripts\python.exe scripts\inspect-camera-decoders.py --probe-load
```

Use `--ffmpeg <absolute-path>` if FFmpeg is not on PATH and `--root <installed-directory>` for an additional installation. The default report is `build/camera-decoders/inventory.json`, an ignored local diagnostic file containing machine-specific paths. It is not an SDK bundle. A successful library load is explicitly distinct from a successful frame decode.

The current machine has 15 main runtimes across Premiere Pro 2026, Media Encoder 2026 and Resolve; all 15 loaded in isolated processes. The Sony frame experiment above is separate from this generic load test.

| Family | Located runtime | Current evidence |
| --- | --- | --- |
| Sony RAW / X-OCN | `SonyRawDev.dll` 5.3 and Sony SMDK companion | DLL loading; three F55 RAW frame decodes in a local experiment. Other Sony variants untested |
| ARRI | `ArriImageSdk.9.dll` 9.0.1 plus four CPU/GPU transform modules | DLL loading only; headers, API initialization and camera fixtures still needed |
| Canon Cinema RAW | `CrmSdk.dll` 2.11 in Adobe / 2.10 in Resolve, scaling library and runtime/resources | DLL loading only; CRM decoding still untested |
| RED | `REDR3D-x64.dll` in the Adobe installations | DLL loading only; exported names require the matching documented SDK interface |
| Blackmagic RAW | `BlackmagicRawAPI.dll` in Resolve | DLL loading only; use the public vendor SDK for documented integration |
| ProRes RAW | `ProResRAW.dll` plus GPU runtimes | DLL loading only; installed FFmpeg also lists its own native `prores_raw` decoder |
| Panasonic | No dedicated Panasonic RAW DLL identified in these roots | Installed FFmpeg lists H.264, HEVC, MPEG-2, DV and ProRes decoders. Actual Panasonic recordings and P2/spanned-clip assembly require validation |

Panasonic support must route by the recording's codec and container. [Panasonic's P2/VariCam documentation](https://pro-av.panasonic.net/en/support/nle/avc-ultra/) lists AVC-Intra and AVC-LongG families; there is no basis here for treating every Panasonic recording as a single proprietary RAW codec. The app must retain card structures where separate audio, metadata or spanned recordings require them. Recognizing a native FFmpeg decoder does not certify that card workflow.

For independent deployment, obtain the applicable runtime and interface from its vendor: [ARRI Image SDK](https://www.arri.com/en/company/the-arri-philosophy/arri-partner-program), [Blackmagic RAW SDK](https://www.blackmagicdesign.com/developer/products/braw/sdk-and-software), [RED R3D SDK](https://www.reddigitalcinema.com/download/r3d-sdk), and the Sony program above. Canon's Cinema RAW video interface still needs vendor confirmation. Installed editor runtimes are also collected in the experimental Windows portable archive in this repository. The archive includes the frozen app, FFmpeg, companion resources, and manifest hashes; it excludes originals, keys, tokens, and library data. All 39 DLL load checks passed, and Sony F55 also decoded a frame from the copied library. The app importer does not yet call these native adapters. Their presence and successful loading do not establish redistribution rights.

## Linking a viewing proxy

The editor registration/local-media API can supply an existing viewing proxy with the original path; it is referenced too, never copied. This is an advanced decoder fallback rather than a step in normal drag-and-drop import. Confirm that it is the complete original clip: same frame rate, first frame, framing and content, without trimming, speed changes, or padding. Retain dialogue/audio and original start timecode. Preserve aspect ratio without cropping or letterboxing. A low-resolution H.264 MP4 works well for playback; use a viewing transform suitable for recognizing the image.

The app checks whatever original duration, rate, resolution/aspect ratio and timecode it can inspect against the proxy. Missing proxy timecode cannot prove start alignment: the explicit confirmation remains necessary. The original and proxy are fingerprinted; changed/offline files stop further use. Search, thumbnails, shape/composition analysis, playback and Gemini requests use the viewing image while the source filename and edit identity remain the original. Proxy modes control additional temporary analysis encoding; Off does not discard an explicitly linked viewing proxy.

For Sony recordings, a bounded adjacent `M01.xml` sidecar supplies recording format, camera/recorder model, resolution and selected acquisition metadata. The exact probed stream rate takes precedence over a rounded `23.98p` label. XML entities/external references are not followed. Serial numbers and arbitrary sidecar contents are not included.

If the original cannot be inspected at all, proxy-derived duration/rate are explicitly marked as unverified original facts. Original resolution is left unknown; native timeline exports may be unavailable until original timing/resolution can be established. The app does not invent RAW source metadata to enable an export.

Camera formats that a browser cannot play get an app-owned, cached H.264/AAC playback proxy on first playback. The workspace shows **Preparing playback proxy** while it is being prepared. That cache is separate from Gemini's lazy ten-minute analysis proxies. Only thumbnails and derived proxies are stored locally; deleting the project preserves the original and any user-provided viewing proxy. Derived playback proxies are currently generated in full, so initial playback of long camera originals can take time.

## Verification

The user's Sony clip was inspected in place on its network share: the adjacent sidecar identifies **F55RAWSQ_4096_2160**, PMW-F55 camera and AXS-R5 recorder. FFprobe reads a 36.703333-second duration, nominal 24000/1001 stream rate, four PCM audio tracks and source timecode, but reports no supported video codec. This is a genuine RAW recording, not XAVC wrapped in MXF. No original bytes were copied or modified. A vendor/editor viewing proxy is still needed to analyze this particular recording.

Automated tests cover linked import without copying, deduplication, unsupported decoder guidance, frame-rate/duration/timecode/aspect mismatches, source/proxy changes, explicit alignment confirmation, preservation on deletion, public path exclusion and same-origin/loopback restrictions. Camera-family claims are conditional on decoding; physical camera fixtures for every family and native RAW SDK builds still need validation.

A generated MPEG-2 4:2:2 MXF fixture was linked, decoded, converted to browser H.264 with audio, and used to produce an analysis part. Its 3,467,833-byte original stayed unchanged; the 611,494-byte playback proxy was written only to the app-owned cache. Browser verification confirmed its preview plays and its original filename, codec and timecode remain attached. This verifies the MXF/container route, not Sony RAW decoding. The full backend suite passed 410 tests and 19 subtests; subsequent camera/editor hardening passed 86 focused checks. Frontend build/lint, pre-transfer copy safeguards and editor integration checks passed.

## Primary references

- [Sony F5/F55 recording formats](https://pro.sony/s3/cms-static-content/file/90/1237490081790.pdf)
- [Sony RAW Viewer guide](https://pro.sony/s3/2019/01/24031021/4461641181.pdf): RAW/X-OCN/XAVC viewing and exports
- [Blackmagic RAW and its SDK](https://www.blackmagicdesign.com/products/blackmagicraw)
- [REDline](https://docs.red.com/955-0004_v42/REV-A/HTML/955-0004_V42%20Rev-A%20%20%20RED%20PS%2C%20REDCINE-X%20PRO%20Operation%20Guide/Content/11_REDLINE/1_Intro_REDLINE.htm)
- [FFmpeg supported formats](https://ffmpeg.org/general.html), [ProRes RAW decoder](https://ffmpeg.org/doxygen/8.1/prores__raw_8c.html)
- [ARRI Reference Tool](https://www.arri.com/en/learn/camera-systems/tools/arri-reference-tool): ARRIRAW processing and ProRes/metadata exports
- [Canon Cinema RAW Light workflow](https://www.usa.canon.com/bin/canon/downloadasset?path=%2Fcontent%2Fdam%2Fcanon-assets%2Fproduct-assets%2FCameras%2Fcinema-eos-cameras%2Fcinema-eos%2Feos-r5-c%2Fresources%2FEOS_R5_C_Quick-Start_Guide_Video.pdf)
- [Panasonic camera recording and proxy codecs](https://pro-av.panasonic.net/en/sales_o/p2/p2_camera_codec.pdf)
