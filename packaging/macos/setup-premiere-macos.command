#!/usr/bin/env bash
# Explicit user action: install a local CA and create the HTTPS certificate.
set -euo pipefail
script_dir="$(cd "$(dirname "$0")" && pwd)"
source "$script_dir/macos-common.sh"
va_require_macos
va_use_homebrew
data_dir="${1:-$HOME/Library/Application Support/VideoAnalyzer}"
[[ "$data_dir" == /* ]] || va_die 'The data directory must be an absolute path.'
tls_dir="$data_dir/tls"
umask 077
mkdir -p "$tls_dir"
chmod 700 "$tls_dir"
if ! command -v mkcert >/dev/null; then
  HOMEBREW_NO_AUTO_UPDATE=1 "$VA_BREW" install mkcert
fi
printf '%s\n' 'Setting up HTTPS for Video Analyzer on this Mac.' 'mkcert will install a local certificate authority into the system trust store.' 'macOS may request an administrator password. This command does not upload certificates or media.'
TRUST_STORES=system mkcert -install
mkcert -cert-file "$tls_dir/localhost.pem" -key-file "$tls_dir/localhost-key.pem" localhost 127.0.0.1 ::1
ca_dir="$(mkcert -CAROOT)"
[[ -f "$ca_dir/rootCA.pem" ]] || va_die 'mkcert did not create its public certificate.'
cp "$ca_dir/rootCA.pem" "$tls_dir/rootCA.pem"
chmod 600 "$tls_dir/localhost-key.pem"
chmod 644 "$tls_dir/localhost.pem" "$tls_dir/rootCA.pem"
printf '%s\n' 'Setup complete. Restart Video Analyzer and open the Premiere panel.' 'The panel connects automatically to https://localhost:8443; no pairing token is needed.' 'The private CA key remains in mkcert storage; only its public certificate was copied to Video Analyzer.'
