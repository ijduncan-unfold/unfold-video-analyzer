#!/usr/bin/env bash
set -euo pipefail
exec /bin/bash "$(cd "$(dirname "$0")" && pwd)/setup-desktop-macos.sh" "$@"
