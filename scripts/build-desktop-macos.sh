#!/usr/bin/env bash
# Run on a Mac of the target architecture; Windows cannot cross-build this app.
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_root"
python_bin="${VIDEO_ANALYZER_BUILD_PYTHON:-$repo_root/.venv/bin/python}"
if [[ ! -x "$python_bin" ]]; then
  printf '%s\n' 'Create a Python 3.11+ .venv first, or set VIDEO_ANALYZER_BUILD_PYTHON.' >&2
  exit 1
fi
"$python_bin" -m pip install -r requirements-desktop.txt
npm --prefix frontend run build
"$python_bin" -m PyInstaller --noconfirm --distpath dist/desktop --workpath build/desktop packaging/desktop.spec
"dist/desktop/Video Analyzer.app/Contents/MacOS/VideoAnalyzer" --smoke-test --no-browser --port 18741 --data-dir "$repo_root/build/desktop/smoke-data"
mkdir -p dist/installers
"$python_bin" scripts/package-premiere.py
mkdir -p "dist/desktop/Video Analyzer.app/Contents/Resources/plugins"
cp "dist/installers/VideoAnalyzer-Premiere-0.1.0.ccx" "dist/desktop/Video Analyzer.app/Contents/Resources/plugins/"
# Development artifact only. A signed/notarized release is a separate step.
ditto -c -k --sequesterRsrc --keepParent "dist/desktop/Video Analyzer.app" "dist/installers/VideoAnalyzer-0.1.0-preview-macos-$(uname -m).zip"
"$python_bin" packaging/build_inventory.py dist/installers/build-environment.json
printf '%s\n' 'Created a development .app/zip. Sign and notarize it before public distribution.'
