#!/bin/zsh
set -euo pipefail

script=${0:A:h}/stage-mac-mini-user.sh
bootout_line=$(grep -n 'launchctl bootout' "$script" | cut -d: -f1)
owner_line=$(grep -n 'bridge_pid=$(lsof' "$script" | cut -d: -f1)
bootstrap_line=$(grep -n 'launchctl bootstrap' "$script" | cut -d: -f1)

if (( bootout_line >= owner_line || owner_line >= bootstrap_line )); then
  print -u2 "stale listener cleanup must run after bootout and before bootstrap"
  exit 1
fi
grep -q 'bridge_command != \*hermes-g2-bridge\*' "$script"
