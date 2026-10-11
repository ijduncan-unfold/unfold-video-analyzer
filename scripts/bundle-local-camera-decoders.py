"""Build a Windows transfer bundle from selected local camera runtimes.

No automatic upload or public-installer inclusion. Does not copy camera originals,
user configuration, editor credentials, or license keys. Library presence/load
success is not a claim of complete format support or redistribution permission.
"""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
import zipfile


FAMILIES = {
    "sony": ("SonyRawDev.dll", "SMDK*.dll"),
    "arri": ("ArriImageSdk.*.dll", "ArriImageSdkTransforms*.dll", "arriimagesdk_plugins/ArriImageSdkTransforms*.dll", "codexhdedecoder.dll"),
    "canon": ("CrmSdk.dll", "CrmRsc*.bin", "CESScalingLib.dll", "libiomp5md.dll", "cudart64_*.dll"),
    "red": ("REDR3D*.dll", "REDDecoder*.dll", "REDCuda*.dll", "REDOpenCL*.dll", "cudart64_*.dll"),
    "blackmagic": ("BlackmagicRawAPI.dll", "DecoderCPU.dll", "DecoderRef.dll", "DecoderCUDA.dll", "DecoderOpenCL.dll", "InstructionSetServices*.dll"),
    "prores-raw": ("ProResRAW.dll", "cudart64_*.dll"),
}
VC_RUNTIMES = ("msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "msvcp140_atomic_wait.dll",
               "msvcp140_codecvt_ids.dll", "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll", "vcomp140.dll")
DENIED_PARTS = {"uploads", "desktop-settings.json", "premiere-token.txt", "editor-service.json", "editor-service.lock"}


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def copy_file(source, destination, provenance, records):
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"Not a regular source file: {source.name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    original_hash = digest(source)
    if original_hash != digest(destination):
        raise ValueError(f"Copy verification failed: {source.name}")
    records.append({"path": destination, "sha256": original_hash,
                    "bytes": destination.stat().st_size, "provenance": provenance})


def probe_child(path, shared):
    try:
        with os.add_dll_directory(str(path.parent)), os.add_dll_directory(str(shared)):
            library = ctypes.CDLL(str(path), winmode=0x1500)
            print(json.dumps({"loaded": bool(library._handle), "decoded_frame": False}))
    except OSError as error:
        print(json.dumps({"loaded": False, "winerror": error.winerror, "error": str(error), "decoded_frame": False}))


def probe_library(path, shared):
    try:
        process = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--probe-child", str(path), "--shared", str(shared)],
                                 capture_output=True, text=True, timeout=20,
                                 env={**os.environ, "PATH": str(shared) + os.pathsep + str(Path(os.environ["WINDIR"]) / "System32")},
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        if process.returncode:
            return {"loaded": False, "exit_code": process.returncode, "error": process.stderr[-1000:]}
        return json.loads(process.stdout)
    except (subprocess.TimeoutExpired, ValueError) as error:
        return {"loaded": False, "error": str(error)}


def main():
    repo = Path(__file__).resolve().parent.parent
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adobe", type=Path, default=program_files / "Adobe" / "Adobe Premiere Pro 2026")
    parser.add_argument("--resolve", type=Path, default=program_files / "Blackmagic Design" / "DaVinci Resolve")
    parser.add_argument("--ffmpeg-root", type=Path)
    parser.add_argument("--red-runtime", type=Path, help="Matching R3D 9.2.1 redistributable win directory")
    parser.add_argument("--red-bridge", type=Path, help="VideoAnalyzerRED.dll built from native/red_bridge.cpp")
    parser.add_argument("--app", type=Path, help="Optional already-built portable VideoAnalyzer directory")
    parser.add_argument("--output", type=Path, default=repo / "dist" / "private" / "VideoAnalyzer-camera-bundle-windows-x64.zip")
    parser.add_argument("--probe-child", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--shared", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("This bundle is Windows x64 only; macOS needs native Apple Silicon runtimes")
    if args.probe_child:
        probe_child(args.probe_child.resolve(strict=True), args.shared.resolve(strict=True))
        return
    if not args.ffmpeg_root:
        parser.error("--ffmpeg-root must specify the installed FFmpeg distribution directory")
    if args.output.suffix.lower() != ".zip":
        parser.error("--output must be a ZIP archive, never a camera-original or runtime file")
    import pefile
    staging = repo / "build" / "private-camera-bundles" / uuid.uuid4().hex
    staging.mkdir(parents=True)
    records, families, probes = [], {}, []
    for family, patterns in FAMILIES.items():
        origin = args.resolve if family == "blackmagic" else args.adobe
        if family == 'red' and args.red_runtime:
            origin = args.red_runtime
            patterns = ('REDR3D-x64.dll', 'REDDecoder-x64.dll', 'REDCuda-x64.dll', 'REDOpenCL-x64.dll')
        destination = staging / "camera-decoders" / family
        seen = set()
        for pattern in patterns:
            for source in sorted(origin.glob(pattern)):
                if source.name.lower() in seen:
                    continue
                seen.add(source.name.lower())
                # Preserve ARRI's plugin directory for its dynamic module search.
                relative = source.relative_to(origin)
                copy_file(source, destination / relative, f"{origin.name}: {source.name}", records)
        if not seen:
            raise ValueError(f"No installed runtime found for {family}")
        families[family] = sorted(seen)
    if args.red_bridge:
        copy_file(args.red_bridge, staging / 'camera-decoders/red/VideoAnalyzerRED.dll',
                  'Video Analyzer read-only RED SDK bridge', records)
        families['red'].append('VideoAnalyzerRED.dll')
    if bool(args.red_bridge) != bool(args.red_runtime):
        raise ValueError('--red-runtime and --red-bridge must be supplied together')
    if args.red_runtime:
        license_file = args.red_runtime.parent / 'SDK License Agreement.pdf'
        copy_file(license_file, staging / 'notices/RED-SDK-License-Agreement.pdf', 'RED SDK supplied license', records)
    shared = staging / "camera-decoders" / "shared"
    system = Path(os.environ["WINDIR"]) / "System32"
    for name in VC_RUNTIMES:
        source = system / name
        if source.is_file():
            copy_file(source, shared / name, "Installed Microsoft Visual C++ runtime", records)
    # Retain third-party notices supplied with the installed decoder host.
    notice = args.resolve / "Documents" / "Third Party Licenses.rtf"
    if notice.is_file():
        copy_file(notice, staging / "notices" / "Resolve-third-party-licenses.rtf", "Installed third-party notices", records)
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        copy_file(args.ffmpeg_root / "bin" / name, staging / "ffmpeg" / name, f"Local FFmpeg distribution: {name}", records)
    for name in ("LICENSE", "README.txt"):
        copy_file(args.ffmpeg_root / name, staging / "notices" / ("ffmpeg-" + name), "Local FFmpeg distribution notice", records)
    if args.app:
        if not (args.app / "VideoAnalyzer.exe").is_file():
            raise ValueError("--app must point to an existing frozen VideoAnalyzer build")
        for source in sorted(args.app.rglob("*")):
            if source.is_symlink():
                raise ValueError("Symlink in frozen app bundle")
            if not source.is_file():
                continue
            relative = source.relative_to(args.app)
            folded = relative.as_posix().lower()
            if any(part.lower() in DENIED_PARTS or part.lower().startswith(".env") for part in relative.parts) or any(prefix in folded for prefix in ("backend/data/", "app/data/")) or source.suffix.lower() in {".sqlite", ".sqlite3", ".db", ".mp4", ".mxf", ".r3d", ".braw"}:
                raise ValueError(f"Private state or original media must not enter bundle: {relative}")
            copy_file(source, staging / "app" / relative, "Frozen Video Analyzer build", records)
    main_names = {"SonyRawDev.dll", "CrmSdk.dll", "BlackmagicRawAPI.dll", "ProResRAW.dll", "REDR3D-x64.dll"}
    for entry in records:
        path = entry["path"]
        if path.suffix.lower() != ".dll" or "camera-decoders" not in path.parts:
            continue
        with pefile.PE(str(path), fast_load=True) as pe:
            if pe.FILE_HEADER.Machine != 0x8664:
                raise ValueError(f"Non-x64 library in Windows x64 bundle: {path.name}")
        status = probe_library(path, shared)
        probes.append({"path": str(path.relative_to(staging)), **status})
        if (path.name in main_names or path.name.startswith("ArriImageSdk.")) and not status.get("loaded"):
            raise ValueError(f"Main library could not load from bundle: {path.name}: {status}")
    # The launcher sets only process-local tool/library paths. No registry or
    # system PATH changes and no camera-original relocation.
    launch = staging / "Run Video Analyzer.cmd"
    launch.write_text('@echo off\nsetlocal\ncd /d "%~dp0"\nset "VIDEO_ANALYZER_DECODER_DIR=%~dp0camera-decoders"\nset "PATH=%~dp0ffmpeg;%PATH%"\nif not exist "%~dp0app\\VideoAnalyzer.exe" (\n  echo This bundle contains decoders only.\n  pause\n  exit /b 1\n)\nstart "" "%~dp0app\\VideoAnalyzer.exe"\n', encoding="utf8")
    readme = staging / "START HERE.txt"
    readme.write_text(
        "Video Analyzer personal transfer bundle - Windows x64\n\n"
        "Extract the entire ZIP. Double-click Run Video Analyzer.cmd. Keep its folders together.\n"
        "The included frozen app and FFmpeg need no separate Python/Node/Adobe/Resolve installation.\n"
        "Drop Sony F55 RAW MXF, Blackmagic BRAW or RED R3D into the desktop window.\n"
        "Keep recording sidecars and numbered RED file parts together.\n"
        "The app references the original in place and automatically prepares a small\n"
        "viewing proxy with progress. A complete F55 clip passed frame/timing/audio checks.\n"
        "Viewing colors are not a calibrated RAW grade. BRAW and RED adapters also\n"
        "passed real-media frame/timing/audio checks. Canon CRM is not enabled.\n"
        "ARRI RAW requires the official ARRI Reference Tool CMD download; see docs/CAMERA_MEDIA.md.\n"
        "Other FFmpeg-decodable camera media receives a viewing proxy automatically.\n"
        "Bundled camera-decoders contain selected runtimes and their companion resources.\n"
        "Manifest hashes record exact components. GPU modules still need the appropriate\n"
        "hardware drivers (OpenCL/CUDA); Windows system libraries are supplied by Windows.\n"
        "Windows DLLs cannot run on macOS; an Apple Silicon bundle must be built separately.\n"
        "No camera originals, Gemini keys, editor tokens or user database are included.\n"
        "Add your Gemini key in Settings on the destination computer.\n"
        "Vendor runtime permissions remain unverified. Inclusion does not change the\n"
        "licenses of these components. Included third-party notices remain applicable.\n",
        encoding="utf8")
    manifest = {"created_at": datetime.now(timezone.utc).isoformat(), "platform": "windows-x64",
                "personal_transfer_bundle": True, "vendor_permissions_verified": False,
                "decoder_adapters_enabled": (["sony_f55_raw", "blackmagic_raw", "ffmpeg_camera"] +
                    (["red_raw"] if args.red_bridge else [])) if args.app else [], "app_included": bool(args.app),
                "families": families, "load_probes": probes,
                "files": [{**entry, "path": str(entry["path"].relative_to(staging))} for entry in records]}
    (staging / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf8")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + "." + uuid.uuid4().hex + ".tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(staging).as_posix())
    temporary.replace(args.output)
    checksum = digest(args.output)
    args.output.with_suffix(args.output.suffix + ".sha256").write_text(f"{checksum}  {args.output.name}\n", encoding="ascii")
    print(json.dumps({"archive": str(args.output.resolve()), "bytes": args.output.stat().st_size,
                      "sha256": checksum, "manifest": str(staging / "manifest.json"),
                      "families": list(families), "load_tests": len(probes),
                      "load_failures": [probe for probe in probes if not probe.get("loaded")]}), flush=True)


if __name__ == "__main__":
    main()
