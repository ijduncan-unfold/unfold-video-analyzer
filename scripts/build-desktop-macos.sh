#!/usr/bin/env bash
# Build a native Apple Silicon preview; no user trust-store changes are made.
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
source "$repo_root/packaging/macos/macos-common.sh"
va_require_macos
skip_dependencies=false
skip_frontend=false
for argument in "$@"; do
  case "$argument" in
    --skip-dependencies) skip_dependencies=true ;;
    --skip-frontend) skip_frontend=true ;;
    *) va_die "Unknown build argument: $argument" ;;
  esac
done
cd "$repo_root"
export PATH="/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:$PATH"
python_bin="${VIDEO_ANALYZER_BUILD_PYTHON:-$repo_root/build/desktop/venv-arm64/bin/python}"
va_require_native_python "$python_bin"
va_require_native_node
va_require_media_tools
xcode-select -p >/dev/null 2>&1 || va_die 'Install the Xcode Command Line Tools with xcode-select --install, then retry.'
command -v openssl >/dev/null || va_die 'OpenSSL is required for the isolated HTTPS smoke test.'
export VIDEO_ANALYZER_TARGET_ARCH=arm64
export VIDEO_ANALYZER_DESKTOP=1
export GOOGLE_API_KEY=''
# Analysis may import app settings. Keep it away from a developer database/key.
export DATABASE_PATH="$repo_root/build/desktop/package-data/library.db"
export UPLOAD_DIR="$repo_root/build/desktop/package-data/uploads"
export EDITOR_TOKEN_PATH="$repo_root/build/desktop/package-data/premiere-token.txt"
mkdir -p dist/installers build/desktop
if [[ "$skip_dependencies" == false ]]; then
  "$python_bin" -m pip install -r requirements-desktop.txt
fi
if [[ "$skip_frontend" == false ]]; then
  npm --prefix frontend ci
  npm --prefix frontend run build
fi
# Include the public panel before BUNDLE signs the app. Do not mutate it later.
"$python_bin" scripts/package-premiere.py
"$python_bin" scripts/package-resolve.py
"$python_bin" -m PyInstaller --noconfirm --distpath dist/desktop --workpath build/desktop/pyinstaller-arm64 packaging/desktop.spec
app_path="$repo_root/dist/desktop/Video Analyzer.app"
smoke_dir="$(mktemp -d "$repo_root/build/desktop/macos-smoke.XXXXXX")"
"$app_path/Contents/MacOS/VideoAnalyzer" --smoke-test --no-browser --port 18741 --data-dir "$smoke_dir"
"$python_bin" packaging/verify_macos_build.py --app "$app_path" --frontend frontend/dist --smoke "$smoke_dir/smoke-test.json"
codesign --verify --deep --strict "$app_path"
version="$("$python_bin" -c 'import json; print(json.load(open("integrations/premiere/manifest.json"))["version"])')"
stem="VideoAnalyzer-$version-preview-macos-arm64"
panel="VideoAnalyzer-Premiere-$version.ccx"
ditto -c -k --sequesterRsrc --keepParent "$app_path" "dist/installers/$stem.zip"
stage_dir="$(mktemp -d "$repo_root/build/desktop/macos-dmg.XXXXXX")"
ditto "$app_path" "$stage_dir/Video Analyzer.app"
ln -s /Applications "$stage_dir/Applications"
cp "dist/installers/$panel" "$stage_dir/$panel"
cp packaging/macos/'Read Me.txt' "$stage_dir/Read Me.txt"
mkdir "$stage_dir/.support"
cp packaging/macos/macos-common.sh packaging/macos/setup-premiere-macos.command packaging/macos/install-media-tools.command "$stage_dir/.support/"
cp packaging/resolve/install-resolve-macos.command packaging/resolve/public-files.json "$stage_dir/.support/"
mkdir "$stage_dir/.support/VideoAnalyzer"
while IFS= read -r public_file; do
  cp "integrations/resolve/$public_file" "$stage_dir/.support/VideoAnalyzer/$public_file"
done < <("$python_bin" -c 'import json; print("\n".join(json.load(open("packaging/resolve/public-files.json"))))')
cat > "$stage_dir/Prepare Premiere on Mac.command" <<'COMMAND'
#!/usr/bin/env bash
set -euo pipefail
exec /bin/bash "$(cd "$(dirname "$0")" && pwd)/.support/setup-premiere-macos.command" "$@"
COMMAND
cat > "$stage_dir/Install FFmpeg.command" <<'COMMAND'
#!/usr/bin/env bash
set -euo pipefail
exec /bin/bash "$(cd "$(dirname "$0")" && pwd)/.support/install-media-tools.command" "$@"
COMMAND
chmod 755 "$stage_dir/Prepare Premiere on Mac.command" "$stage_dir/Install FFmpeg.command"
cat > "$stage_dir/Install Resolve on Mac.command" <<'COMMAND'
#!/usr/bin/env bash
set -euo pipefail
exec /bin/bash "$(cd "$(dirname "$0")" && pwd)/.support/install-resolve-macos.command" "$@"
COMMAND
chmod 755 "$stage_dir/Install Resolve on Mac.command"
hdiutil create -ov -format UDZO -volname 'Video Analyzer' -srcfolder "$stage_dir" "dist/installers/$stem.dmg"
"$python_bin" packaging/build_inventory.py dist/installers/build-environment-macos-arm64.json
cp "$smoke_dir/smoke-test.json" dist/installers/smoke-test-macos-arm64.json
(cd dist/installers && shasum -a 256 "$stem.zip" "$stem.dmg" "$panel" "VideoAnalyzer-Resolve-$version.zip" > SHA256SUMS-macos-arm64.txt)
printf '%s\n' "Created dist/installers/$stem.zip and .dmg." 'The preview is ad hoc signed unless VIDEO_ANALYZER_CODESIGN_IDENTITY is configured. Public distribution still requires Developer ID signing and notarization.' 'The isolated HTTPS smoke test passed without installing a CA. Complete real Premiere testing on a Mac.'
