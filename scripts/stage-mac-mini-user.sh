#!/bin/zsh
set -euo pipefail

project_dir=${0:A:h:h}
hermes_root=${HERMES_HOME:-$HOME/.hermes}
runtime_root=$hermes_root/hermes-g2
install_root=$runtime_root/install
state_root=$runtime_root/state
log_root=$runtime_root/logs
env_file=$runtime_root/bridge.env
agent_python=$hermes_root/hermes-agent/venv/bin/python
launch_agent=$HOME/Library/LaunchAgents/com.honey.hermes-g2.bridge.plist

if [[ ! -x $agent_python ]]; then
  print -u2 "Hermes Python 3.11 was not found at $agent_python"
  exit 2
fi

mkdir -p "$install_root" "$state_root/models" "$log_root" "$HOME/Library/LaunchAgents"
rsync -a --delete --exclude .venv --exclude .pytest_cache --exclude .ruff_cache --exclude __pycache__ \
  "$project_dir/services/bridge/" "$install_root/bridge/"

if [[ ! -x $install_root/venv/bin/python ]]; then
  "$agent_python" -m venv "$install_root/venv"
fi
"$install_root/venv/bin/pip" install --disable-pip-version-check --quiet "$install_root/bridge"
sdk_major=0
if command -v xcrun >/dev/null 2>&1; then
  sdk_major=$(xcrun --show-sdk-version 2>/dev/null | cut -d. -f1 || true)
fi
if [[ $sdk_major == <-> && $sdk_major -ge 26 ]]; then
  swift build -c release --package-path "$project_dir/services/apple-summary-helper" >/dev/null
  mkdir -p "$install_root/bin"
  cp "$project_dir/services/apple-summary-helper/.build/release/hermes-g2-summary" "$install_root/bin/"
fi

HERMES_G2_RUNTIME_ROOT="$runtime_root" HERMES_G2_ENV_FILE="$env_file" "$agent_python" <<'PY'
import os
import secrets
from pathlib import Path

home = Path.home()
source = home / ".hermes" / ".env"
target = Path(os.environ["HERMES_G2_ENV_FILE"])
runtime = Path(os.environ["HERMES_G2_RUNTIME_ROOT"])

def parse(path):
    values = {}
    if path.exists():
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip("\"'")
    return values

hermes = parse(source)
existing = parse(target)
api_key = hermes.get("API_SERVER_KEY")
if not api_key:
    raise SystemExit("API_SERVER_KEY is missing from ~/.hermes/.env")

values = {
    "HERMES_G2_HERMES_ORIGIN": "http://127.0.0.1:8642",
    "HERMES_G2_HERMES_API_KEY": api_key,
    "HERMES_G2_PLUGIN_SECRET": existing.get("HERMES_G2_PLUGIN_SECRET", secrets.token_urlsafe(48)),
    "HERMES_G2_DATABASE_PATH": str(runtime / "state" / "hermes-g2.db"),
    "HERMES_G2_WHISPER_BINARY": "/opt/homebrew/bin/whisper-cli",
    "HERMES_G2_WHISPER_MODEL": str(runtime / "state" / "models" / "ggml-tiny.en-q5_1.bin"),
    "HERMES_G2_BIND_HOST": "127.0.0.1",
    "HERMES_G2_BIND_PORT": "8765",
    "HERMES_G2_EXTERNAL_BASE_PATH": "/hermes-g2",
    "HERMES_G2_SUMMARY_HELPER": str(runtime / "install" / "bin" / "hermes-g2-summary"),
}
target.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
target.chmod(0o600)
PY

sed \
  -e "s|@@INSTALL_ROOT@@|$install_root|g" \
  -e "s|@@STATE_ROOT@@|$state_root|g" \
  -e "s|@@ENV_FILE@@|$env_file|g" \
  -e "s|@@LOG_ROOT@@|$log_root|g" \
  "$project_dir/deploy/launchd/com.honey.hermes-g2.bridge.user.plist.in" > "$launch_agent"
chmod 0644 "$launch_agent"
plutil -lint "$launch_agent" >/dev/null

launch_domain="gui/$UID"
if ! launchctl print "$launch_domain" >/dev/null 2>&1; then
  launch_domain="user/$UID"
fi
launchctl bootout "$launch_domain/com.honey.hermes-g2.bridge" 2>/dev/null || true
bridge_pid=$(lsof -nP -tiTCP:8765 -sTCP:LISTEN 2>/dev/null || true)
if [[ -n $bridge_pid ]]; then
  if [[ $bridge_pid != <-> ]]; then
    print -u2 "Port 8765 has multiple listeners; refusing an ambiguous replacement."
    exit 3
  fi
  bridge_command=$(ps -p "$bridge_pid" -o command= 2>/dev/null || true)
  if [[ $bridge_command != *hermes-g2-bridge* ]]; then
    print -u2 "Port 8765 is owned by an unexpected process; refusing to replace it."
    exit 3
  fi
  kill "$bridge_pid"
  for attempt in {1..20}; do
    kill -0 "$bridge_pid" 2>/dev/null || break
    sleep 0.1
  done
  if kill -0 "$bridge_pid" 2>/dev/null; then
    print -u2 "The previous Hermes G2 bridge did not stop cleanly."
    exit 3
  fi
fi
if launchctl bootstrap "$launch_domain" "$launch_agent"; then
  launchctl enable "$launch_domain/com.honey.hermes-g2.bridge"
else
  nohup env HERMES_G2_ENV_FILE="$env_file" "$install_root/venv/bin/hermes-g2-bridge" serve \
    >"$log_root/bridge.log" 2>"$log_root/bridge.error.log" </dev/null &
  launch_domain="detached (until the next login loads the installed LaunchAgent)"
fi

for attempt in {1..20}; do
  if curl -fsS http://127.0.0.1:8765/health >/dev/null; then
    break
  fi
  sleep 0.25
done
curl -fsS http://127.0.0.1:8765/health >/dev/null

print "Staged user-domain bridge on 127.0.0.1:8765 ($launch_domain)."
print "This is a functional deployment tier, but it does not replace the planned admin-installed LaunchDaemon."
