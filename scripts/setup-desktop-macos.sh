#!/usr/bin/env bash
# One-command source setup/build. This does not install a CA or alter Keychain.
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
source "$repo_root/packaging/macos/macos-common.sh"
va_require_macos
va_use_homebrew
HOMEBREW_NO_AUTO_UPDATE=1 "$VA_BREW" install python@3.12 node@22 ffmpeg
export PATH="$("$VA_BREW" --prefix node@22)/bin:$PATH"
python_bin="$("$VA_BREW" --prefix python@3.12)/bin/python3.12"
va_require_native_python "$python_bin"
va_require_native_node
va_require_media_tools
cd "$repo_root"
venv_dir="$repo_root/build/desktop/venv-arm64"
if [[ ! -x "$venv_dir/bin/python" ]]; then
  "$python_bin" -m venv "$venv_dir"
fi
export VIDEO_ANALYZER_BUILD_PYTHON="$venv_dir/bin/python"
va_require_native_python "$VIDEO_ANALYZER_BUILD_PYTHON"
"$VIDEO_ANALYZER_BUILD_PYTHON" -m pip install -r requirements-desktop.txt -r backend/requirements-dev.txt
npm --prefix frontend ci
"$VIDEO_ANALYZER_BUILD_PYTHON" -m pytest -q
"$VIDEO_ANALYZER_BUILD_PYTHON" -m unittest discover -s packaging -p test_verify_macos_build.py
node --test integrations/premiere/tests/*.test.cjs
npm --prefix frontend run lint
npm --prefix frontend run build
exec /bin/bash "$repo_root/scripts/build-desktop-macos.sh" --skip-dependencies --skip-frontend
