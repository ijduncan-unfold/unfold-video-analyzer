"""Build from the repository root; only allowlisted public resources enter the app."""
from pathlib import Path
import os
import json
import platform
import sys
from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

root = Path(SPECPATH).parent.resolve()
frontend = root / "frontend" / "dist"
if not (frontend / "index.html").is_file():
    raise SystemExit("Build frontend first: npm --prefix frontend run build")

datas = [(str(frontend), "frontend/dist")]
python_license = next(iter(Path(sys.base_prefix).glob("LICENSE*")), None)
if python_license is not None:
    datas.append((str(python_license), "desktop-docs/python"))
binaries = []
hiddenimports = collect_submodules("app")
for package in ("google.genai", "pystray", "uvicorn", "webview"):
    package_data, package_bins, package_imports = collect_all(
        package, include_py_files=False,
        filter_submodules=lambda name: ".tests" not in name and "._test" not in name,
        exclude_datas=["**/tests/**", "**/_test*"],
    )
    datas += package_data
    binaries += package_bins
    hiddenimports += package_imports
for package in ("google-genai", "pydantic", "pydantic-settings", "fastapi", "uvicorn",
                "pystray", "Pillow", "numpy", "opencv-python-headless", "pywebview", "bottle", "proxy_tools"):
    datas += copy_metadata(package)
if sys.platform == 'win32':
    for package in ('pythonnet', 'clr_loader'):
        datas += copy_metadata(package)
datas += copy_metadata("pyinstaller")
datas += [(str(root / "packaging" / "README.md"), "desktop-docs"),
          (str(root / "packaging" / "THIRD_PARTY.md"), "desktop-docs")]

# Public Resolve UI only. The native bridge comes from the user's installed SDK.
resolve_files = json.loads((root / 'packaging/resolve/public-files.json').read_text(encoding='utf-8'))
for name in resolve_files:
    path = root / 'integrations/resolve' / name
    if path.is_symlink() or not path.is_file() or Path(name).name != name or '/' in name or '\\' in name or name.endswith('.node'):
        raise SystemExit('A public Resolve panel resource is missing or unsafe.')
    datas.append((str(path), 'resolve-panel'))
for name in ('install-resolve.ps1', 'install-resolve-macos.command', 'public-files.json'):
    datas.append((str(root / 'packaging/resolve' / name), 'desktop-docs'))

panel = root / "dist" / "installers" / "VideoAnalyzer-Premiere-0.1.19.ccx"
if not panel.is_file():
    raise SystemExit("Package the Premiere panel before freezing the app.")
datas.append((str(panel), "plugins"))

mac_exe_options = {}
if sys.platform == "darwin":
    if platform.machine() != "arm64" or os.getenv("VIDEO_ANALYZER_TARGET_ARCH", "arm64") != "arm64":
        raise SystemExit("The macOS preview must be built natively on Apple Silicon.")
    datas += [(str(root / "packaging" / "macos" / "setup-premiere-macos.command"), "desktop-docs"),
              (str(root / "packaging" / "macos" / "macos-common.sh"), "desktop-docs")]
    for package in ("pyobjc-core", "pyobjc-framework-Cocoa", "pyobjc-framework-Quartz"):
        datas += copy_metadata(package)
    mac_exe_options = {
        "target_arch": "arm64",
        "codesign_identity": os.getenv("VIDEO_ANALYZER_CODESIGN_IDENTITY") or None,
        "entitlements_file": str(root / "packaging" / "macos" / "entitlements.plist"),
    }
a = Analysis([str(root / "backend" / "app" / "desktop.py")],
             pathex=[str(root / "backend")], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports, hookspath=[], hooksconfig={},
             runtime_hooks=[], excludes=["tkinter", "pytest", "IPython", "matplotlib"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="VideoAnalyzer",
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False, **mac_exe_options)
collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="VideoAnalyzer")
if sys.platform == "darwin":
    app = BUNDLE(collection, name="Video Analyzer.app", bundle_identifier="com.ijduncan.videoanalyzer",
                 info_plist={"CFBundleShortVersionString": "0.1.0", "CFBundleVersion": "0.1.0",
                             "CFBundleName": "Video Analyzer", "CFBundleDisplayName": "Video Analyzer",
                             "LSUIElement": True, "LSMinimumSystemVersion": "15.0",
                             "LSArchitecturePriority": ["arm64"], "NSHighResolutionCapable": True,
                             "NSAppleEventsUsageDescription": "Open Terminal when you choose to install the Resolve integration or prepare your editor's trusted local HTTPS connection.",
                             "NSDocumentsFolderUsageDescription": "Analyze source media you select from Documents.",
                             "NSDownloadsFolderUsageDescription": "Analyze source media you select from Downloads.",
                             "NSDesktopFolderUsageDescription": "Analyze source media you select from Desktop."})
