#!/bin/zsh
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  print -u2 "Run with sudo on the Mac mini."
  exit 1
fi
if ! command -v tailscale >/dev/null; then
  print -u2 "Tailscale CLI is unavailable."
  exit 2
fi

tailscale serve --bg --https=443 --set-path=/hermes-g2 http://127.0.0.1:8765
print "Only the bridge is served. Hermes remains on 127.0.0.1:8642."

