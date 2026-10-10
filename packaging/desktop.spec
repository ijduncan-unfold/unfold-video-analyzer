"""Build from the repository root; only allowlisted public resources enter the app."""
from pathlib import Path
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
for package in ("google.genai", "pystray", "uvicorn"):
    package_data, package_bins, package_imports = collect_all(
        package, include_py_files=False,
        filter_submodules=lambda name: ".tests" not in name and "._test" not in name,
        exclude_datas=["**/tests/**", "**/_test*"],
    )
    datas += package_data
    binaries += package_bins
    hiddenimports += package_imports
for package in ("google-genai", "pydantic", "pydantic-settings", "fastapi", "uvicorn",
                "pystray", "Pillow", "numpy", "opencv-python-headless", "reportlab"):
    datas += copy_metadata(package)
datas += copy_metadata("pyinstaller")
datas += [(str(root / "packaging" / "README.md"), "desktop-docs"),
          (str(root / "packaging" / "THIRD_PARTY.md"), "desktop-docs")]
a = Analysis([str(root / "backend" / "app" / "desktop.py")],
             pathex=[str(root / "backend")], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports, hookspath=[], hooksconfig={},
             runtime_hooks=[], excludes=["tkinter", "pytest", "IPython", "matplotlib"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="VideoAnalyzer",
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False)
collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="VideoAnalyzer")
if sys.platform == "darwin":
    app = BUNDLE(collection, name="Video Analyzer.app", bundle_identifier="com.ijduncan.videoanalyzer",
                 info_plist={"CFBundleShortVersionString": "0.1.0", "LSUIElement": True})
