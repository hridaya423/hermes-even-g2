#!/bin/zsh
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  print -u2 "Run with sudo on the Mac mini."
  exit 1
fi

project_dir=${0:A:h:h}
install_root=/opt/hermes-g2
state_root=/var/lib/hermes-g2
config_root=/etc/hermes-g2
log_root=/var/log/hermes-g2
invoking_user=${SUDO_USER:-}
staged_root=""
if [[ -n $invoking_user && $invoking_user != root ]]; then
  staged_root="/Users/$invoking_user/.hermes/hermes-g2"
fi

install -d -m 0755 "$install_root" "$state_root" "$state_root/models" "$log_root"
install -d -m 0700 "$config_root"
if [[ ! -f "$config_root/bridge.env" ]]; then
  if [[ -n $staged_root && -f "$staged_root/bridge.env" ]]; then
    STAGED_ENV="$staged_root/bridge.env" TARGET_ENV="$config_root/bridge.env" python3 <<'PY'
import os
from pathlib import Path

source = Path(os.environ["STAGED_ENV"])
target = Path(os.environ["TARGET_ENV"])
values = {}
for raw in source.read_text().splitlines():
    line = raw.strip()
    if line and not line.startswith("#") and "=" in line:
        key, value = line.split("=", 1)
        values[key] = value
for required in ("HERMES_G2_HERMES_API_KEY", "HERMES_G2_PLUGIN_SECRET"):
    if not values.get(required):
        raise SystemExit(f"{required} is missing from the staged environment")
values.update(
    HERMES_G2_DATABASE_PATH="/var/lib/hermes-g2/hermes-g2.db",
    HERMES_G2_WHISPER_MODEL="/var/lib/hermes-g2/models/ggml-tiny.en-q5_1.bin",
    HERMES_G2_SUMMARY_HELPER="/opt/hermes-g2/bin/hermes-g2-summary",
)
target.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
target.chmod(0o600)
PY
  else
    install -m 0600 "$project_dir/deploy/hermes-g2.env.example" "$config_root/bridge.env"
    print -u2 "No verified user-tier environment was found. Edit $config_root/bridge.env, then rerun."
    exit 2
  fi
fi
chmod 0600 "$config_root/bridge.env"
rsync -a --delete --exclude .venv --exclude .pytest_cache --exclude .ruff_cache --exclude __pycache__ "$project_dir/services/bridge/" "$install_root/bridge/"
python3.11 -m venv "$install_root/venv"
"$install_root/venv/bin/pip" install --disable-pip-version-check "$install_root/bridge"
if [[ -x $project_dir/services/apple-summary-helper/.build/release/hermes-g2-summary ]]; then
  install -d -m 0755 "$install_root/bin"
  install -m 0755 "$project_dir/services/apple-summary-helper/.build/release/hermes-g2-summary" "$install_root/bin/hermes-g2-summary"
else
  print -u2 "Apple summary helper is absent; deterministic local summaries remain enabled."
fi
if [[ -n $staged_root && -f "$staged_root/state/hermes-g2.db" && ! -f "$state_root/hermes-g2.db" ]]; then
  sqlite3 "$staged_root/state/hermes-g2.db" ".backup '$state_root/hermes-g2.db'"
  chmod 0600 "$state_root/hermes-g2.db"
fi
if [[ -n $staged_root && -f "$staged_root/state/models/ggml-tiny.en-q5_1.bin" ]]; then
  install -m 0644 "$staged_root/state/models/ggml-tiny.en-q5_1.bin" "$state_root/models/ggml-tiny.en-q5_1.bin"
fi
install -m 0644 "$project_dir/deploy/launchd/com.honey.hermes-g2.bridge.plist" /Library/LaunchDaemons/com.honey.hermes-g2.bridge.plist
if [[ -n $invoking_user && $invoking_user != root ]]; then
  invoking_uid=$(id -u "$invoking_user")
  launchctl bootout "user/$invoking_uid/com.honey.hermes-g2.bridge" 2>/dev/null || true
fi
staged_pid=$(lsof -nP -tiTCP:8765 -sTCP:LISTEN 2>/dev/null || true)
if [[ $staged_pid == <-> ]]; then
  staged_command=$(ps -p "$staged_pid" -o command= 2>/dev/null || true)
  if [[ $staged_command == *hermes-g2-bridge* ]]; then
    kill "$staged_pid"
  else
    print -u2 "Port 8765 is owned by an unexpected process; refusing to replace it."
    exit 3
  fi
fi
launchctl bootout system/com.honey.hermes-g2.bridge 2>/dev/null || true
launchctl bootstrap system /Library/LaunchDaemons/com.honey.hermes-g2.bridge.plist
launchctl enable system/com.honey.hermes-g2.bridge
for attempt in {1..40}; do
  curl -fsS http://127.0.0.1:8765/health >/dev/null 2>&1 && break
  sleep 0.25
done
curl -fsS http://127.0.0.1:8765/health >/dev/null
print "Bridge promoted to the boot-level LaunchDaemon with staged credentials and device state preserved."
