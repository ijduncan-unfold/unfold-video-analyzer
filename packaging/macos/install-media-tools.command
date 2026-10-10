#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "$0")" && pwd)/macos-common.sh"
va_require_macos
va_use_homebrew
printf '%s\n' 'Installing FFmpeg and ffprobe through native Homebrew. Existing installations will be reused.'
HOMEBREW_NO_AUTO_UPDATE=1 "$VA_BREW" install ffmpeg
va_require_media_tools
printf '%s\n' 'Media tools are ready. Restart Video Analyzer.'
