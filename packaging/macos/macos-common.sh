#!/usr/bin/env bash
# Shared native-platform checks; sourcing this file has no side effects.
va_die() { printf 'Video Analyzer: %s\n' "$*" >&2; exit 1; }

va_require_macos() {
  [[ "$(uname -s)" == Darwin ]] || va_die 'Run this command on an Apple Silicon Mac.'
  local translated
  translated="$(/usr/sbin/sysctl -in sysctl.proc_translated 2>/dev/null || true)"
  [[ "$translated" != 1 ]] || va_die 'Open a native Terminal with Open using Rosetta disabled, then try again.'
  [[ "$(uname -m)" == arm64 ]] || va_die 'This preview supports Apple Silicon Macs only.'
}

va_use_homebrew() {
  [[ -x /opt/homebrew/bin/brew ]] || va_die 'Install native Homebrew from https://brew.sh, then run this command again.'
  VA_BREW=/opt/homebrew/bin/brew
  eval "$("$VA_BREW" shellenv)"
  [[ "$("$VA_BREW" --prefix)" == /opt/homebrew ]] || va_die 'An Apple Silicon Homebrew installation is required.'
}

va_require_native_python() {
  [[ -x "$1" ]] || va_die 'Run bash scripts/setup-desktop-macos.sh first, or set VIDEO_ANALYZER_BUILD_PYTHON to a native Python executable.'
  "$1" -c 'import platform,sys; sys.exit(0 if platform.machine()=="arm64" and sys.version_info>=(3,11) else 1)' || va_die 'Python must run natively as arm64 and be version 3.11 or newer.'
}

va_require_native_node() {
  command -v node >/dev/null || va_die 'Node.js 22 or newer is required. Run the Mac setup script.'
  node -e 'process.exit(process.arch === "arm64" && Number(process.versions.node.split(".")[0]) >= 22 ? 0 : 1)' || va_die 'Node.js must run natively as arm64 and be version 22 or newer.'
}

va_require_media_tools() {
  local command_name executable architecture
  for command_name in ffmpeg ffprobe; do
    executable="$(command -v "$command_name" || true)"
    [[ -n "$executable" ]] || va_die 'FFmpeg and ffprobe are required. Run Install FFmpeg.command or brew install ffmpeg.'
    architecture="$(/usr/bin/lipo -archs "$executable" 2>/dev/null || true)"
    [[ " $architecture " == *' arm64 '* ]] || va_die "$command_name must include a native arm64 executable."
  done
}
