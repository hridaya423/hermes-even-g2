#!/bin/zsh
set -euo pipefail

project_dir=${0:A:h:h}
hermes_root=${HERMES_HOME:-$HOME/.hermes}
bridge_env=$hermes_root/hermes-g2/bridge.env
plugin_target=$hermes_root/plugins/hermes_g2_observer
hermes_env=$hermes_root/.env

if [[ ! -f $bridge_env ]]; then
  print -u2 "Stage the bridge before installing its Hermes observer."
  exit 2
fi

mkdir -p "$plugin_target"
rsync -a --delete --exclude tests --exclude __pycache__ \
  "$project_dir/packages/hermes-plugin/" "$plugin_target/"

HERMES_G2_BRIDGE_ENV="$bridge_env" HERMES_G2_HERMES_ENV="$hermes_env" python3 <<'PY'
import os
from pathlib import Path

bridge_path = Path(os.environ["HERMES_G2_BRIDGE_ENV"])
hermes_path = Path(os.environ["HERMES_G2_HERMES_ENV"])

def parse(path):
    values = {}
    if path.exists():
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip("\"'")
    return values

secret = parse(bridge_path).get("HERMES_G2_PLUGIN_SECRET")
if not secret:
    raise SystemExit("The bridge plugin secret is missing")

updates = {
    "HERMES_G2_PLUGIN_ORIGIN": "http://127.0.0.1:8765",
    "HERMES_G2_PLUGIN_SECRET": secret,
}
lines = hermes_path.read_text().splitlines() if hermes_path.exists() else []
remaining = dict(updates)
output = []
for line in lines:
    key = line.split("=", 1)[0].strip() if "=" in line else ""
    if key in remaining:
        output.append(f"{key}={remaining.pop(key)}")
    else:
        output.append(line)
output.extend(f"{key}={value}" for key, value in remaining.items())
hermes_path.write_text("\n".join(output) + "\n")
hermes_path.chmod(0o600)
PY

print "Hermes G2 observer staged. Restart Hermes only after any in-progress Hermes repair is complete."
