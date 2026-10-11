"""Inventory locally installed Windows camera runtimes without copying them.

Run with the desktop-build Python environment (which includes pefile). Reports
are diagnostics, not a registry of supported formats. --probe-load only loads
each library in an isolated process; it never invokes an undocumented decoder
API, patches a runtime, starts an editor, or reads camera originals.
"""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


PATTERNS = {
    "sony_raw": "SonyRawDev.dll",
    "arri_raw": "ArriImageSdk.*.dll",
    "canon_cinema_raw": "CrmSdk.dll",
    "red_r3d": "REDR3D*.dll",
    "blackmagic_raw": "BlackmagicRawAPI.dll",
    "prores_raw": "ProResRAW.dll",
}
COMPANIONS = {
    "arri_raw": ("ArriImageSdkTransforms*.dll", "arriimagesdk_plugins/ArriImageSdkTransforms*.dll"),
    "canon_cinema_raw": ("CrmRsc*.bin", "CESScalingLib.dll", "cudart64_*.dll"),
    "prores_raw": ("cudart64_*.dll",),
    "sony_raw": ("SMDK*.dll",),
}
FFMPEG_CODECS = ("h264", "hevc", "mpeg2video", "dvvideo", "prores", "prores_raw", "dnxhd", "cfhd", "jpeg2000")


def installed_roots():
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    adobe = program_files / "Adobe"
    roots = []
    if adobe.is_dir():
        for pattern in ("Adobe Premiere Pro *", "Adobe Media Encoder *"):
            roots.extend(path for path in adobe.glob(pattern) if path.is_dir())
    for path in (program_files / "Blackmagic Design" / "DaVinci Resolve",
                 program_files / "Blackmagic Design" / "Blackmagic RAW"):
        if path.is_dir():
            roots.append(path)
    return sorted(set(roots))


def load_child(path):
    """Use only the DLL's own folder and Windows system directories."""
    try:
        with os.add_dll_directory(str(path.parent)):
            library = ctypes.CDLL(str(path), winmode=0x1100)
            result = {"loaded": bool(library._handle), "decoder_api_tested": False}
    except OSError as error:
        result = {"loaded": False, "winerror": error.winerror, "error": str(error), "decoder_api_tested": False}
    print(json.dumps(result))


def probe_load(path):
    try:
        process = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--load-child", str(path)],
            capture_output=True, text=True, timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if process.returncode:
            return {"loaded": False, "process_exit_code": process.returncode,
                    "error": process.stderr[-1000:], "decoder_api_tested": False}
        return json.loads(process.stdout)
    except subprocess.TimeoutExpired:
        return {"loaded": False, "error": "Library load exceeded 20 seconds", "decoder_api_tested": False}
    except ValueError:
        return {"loaded": False, "error": "Library emitted unexpected diagnostic output", "decoder_api_tested": False}


def inspect_runtime(path, family, probe):
    import pefile
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    with pefile.PE(str(path)) as pe:
        exports = [item.name.decode("ascii", "replace") for item in
                   getattr(pe, "DIRECTORY_ENTRY_EXPORT", type("Empty", (), {"symbols": []})()).symbols if item.name]
        dependencies = [item.dll.decode("ascii", "replace") for item in getattr(pe, "DIRECTORY_ENTRY_IMPORT", [])]
        delay_dependencies = [item.dll.decode("ascii", "replace") for item in getattr(pe, "DIRECTORY_ENTRY_DELAY_IMPORT", [])]
        version = None
        info = getattr(pe, "VS_FIXEDFILEINFO", [])
        if info:
            value = info[0]
            version = ".".join(str(part) for part in (
                value.FileVersionMS >> 16, value.FileVersionMS & 0xffff,
                value.FileVersionLS >> 16, value.FileVersionLS & 0xffff))
        result = {
            "family": family, "path": str(path), "bytes": path.stat().st_size,
            "sha256": digest,
            "file_version": version, "pe_machine": hex(pe.FILE_HEADER.Machine),
            "dependencies": dependencies, "delay_dependencies": delay_dependencies,
            "exports": exports, "export_count": len(exports),
            "components": sorted({str(component) for pattern in COMPANIONS.get(family, ())
                                  for component in path.parent.glob(pattern) if component.is_file()}),
            "usable_for_decoding": "unverified", "redistribution_permission": "unverified",
        }
    if probe:
        result["load_probe"] = probe_load(path)
    return result


def inspect_ffmpeg(executable):
    try:
        process = subprocess.run([executable, "-hide_banner", "-decoders"],
                                 capture_output=True, text=True, timeout=15,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        if process.returncode:
            return {"executable": executable, "process_exit_code": process.returncode,
                    "error": process.stderr[-1000:]}
        names = {parts[1] for line in process.stdout.splitlines()
                 if len(parts := line.split()) >= 2 and len(parts[0]) == 6 and parts[0][0] == "V"}
        return {"executable": executable, "process_exit_code": process.returncode,
                "decoders": {name: name in names for name in FFMPEG_CODECS},
                "camera_fixture_tested": False}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"executable": executable, "error": str(error)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, action="append", help="Additional installed runtime directory")
    parser.add_argument("--probe-load", action="store_true", help="Test DLL loading in isolated processes")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="Path to installed FFmpeg")
    parser.add_argument("--output", type=Path, default=Path("build/camera-decoders/inventory.json"))
    parser.add_argument("--load-child", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("This inspection tool currently targets Windows DLLs")
    if args.load_child:
        load_child(args.load_child.resolve(strict=True))
        return
    roots = set(installed_roots())
    for root in args.root or []:
        resolved = root.resolve(strict=True)
        if not resolved.is_dir():
            parser.error("--root must be an installed runtime directory")
        roots.add(resolved)
    runtimes, errors = [], []
    for root in sorted(roots):
        for family, pattern in PATTERNS.items():
            for path in sorted(root.glob(pattern)):
                try:
                    runtimes.append(inspect_runtime(path, family, args.probe_load))
                except (OSError, ValueError) as error:
                    errors.append({"path": str(path), "error": str(error)})
    report = {"generated_at": datetime.now(timezone.utc).isoformat(),
              "binaries_copied": False, "camera_originals_read": False,
              "roots": [str(path) for path in sorted(roots)], "runtimes": runtimes,
              "ffmpeg": inspect_ffmpeg(args.ffmpeg), "errors": errors}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf8")
    for runtime in runtimes:
        probe = runtime.get("load_probe", {})
        status = "loads" if probe.get("loaded") else "load failed" if probe else "not load-tested"
        print(f"{runtime['family']}: {runtime['path']} ({status}; decoding unverified)")
    print(f"Report: {args.output.resolve()}")


if __name__ == "__main__":
    main()
