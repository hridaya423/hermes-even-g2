#!/bin/zsh
set -euo pipefail

# Read-only production audit. Run this on the Mac mini after a repair or reboot;
# it deliberately refuses to restart Hermes or mutate credentials/services.
project_dir=${0:A:h:h}
hermes_root=${HERMES_HOME:-$HOME/.hermes}
hermes_checkout=${HERMES_CHECKOUT:-$hermes_root/hermes-agent}
bridge_env=${HERMES_G2_ENV_FILE:-$hermes_root/hermes-g2/bridge.env}
bridge_binary=${HERMES_G2_BRIDGE_BINARY:-$hermes_root/hermes-g2/install/venv/bin/hermes-g2-bridge}
patch_file=$project_dir/patches/hermes-session-run-control.patch

set +e
hermes_status=$(git -C "$hermes_checkout" status --short 2>/dev/null)
hermes_status_code=$?
set -e

patch_state="not_checked"
if [[ $hermes_status_code -eq 0 && -z $hermes_status ]]; then
  if git -C "$hermes_checkout" apply --reverse --check "$patch_file" >/dev/null 2>&1; then
    patch_state="applied"
  elif git -C "$hermes_checkout" apply --check "$patch_file" >/dev/null 2>&1; then
    patch_state="not_applied"
  else
    patch_state="mismatch"
  fi
fi

bridge_health="down"
if curl --max-time 3 -fsS http://127.0.0.1:8765/health >/dev/null 2>&1; then
  bridge_health="up"
fi

hermes_health="down"
if curl --max-time 3 -fsS http://127.0.0.1:8642/health >/dev/null 2>&1; then
  hermes_health="up"
fi

system_service="absent"
if launchctl print system/com.honey.hermes-g2.bridge >/dev/null 2>&1; then
  system_service="loaded"
fi

plugin_state="absent"
if [[ -f $hermes_root/plugins/hermes_g2_observer/hermes_g2_plugin/plugin.py && -f $bridge_env ]]; then
  plugin_state="staged"
  plugin_state=$($hermes_root/hermes-agent/venv/bin/python - <<'PY' 2>/dev/null || print "staged"
import sys
from pathlib import Path

root = Path.home() / ".hermes" / "plugins" / "hermes_g2_observer"
sys.path.insert(0, str(root))
import hermes_g2_plugin.plugin as plugin

registered = []
class Context:
    def register_hook(self, name, callback):
        registered.append(name)
plugin.register(Context())
expected = {"pre_approval_request", "post_approval_response", "session_start", "session_end"}
print("active" if expected.issubset(registered) else "staged")
PY
)
fi

tailscale_state="unknown"
if command -v tailscale >/dev/null 2>&1; then
  tailscale_state=$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys; print("running" if json.load(sys.stdin).get("BackendState") == "Running" else "not_running")' 2>/dev/null || print "error")
fi

python3 - "$hermes_status_code" "$patch_state" "$bridge_health" "$hermes_health" "$system_service" "$plugin_state" "$tailscale_state" <<'PY'
import json
import sys

keys = [
    "hermesCheckoutReadable", "patchState", "bridgeHealth", "hermesHealth",
    "systemService", "pluginState", "tailscaleState",
]
values = sys.argv[1:]
result = dict(zip(keys, values))
result["hermesCheckoutReadable"] = result["hermesCheckoutReadable"] == "0"
result["hermesWorkingTreeClean"] = result["hermesCheckoutReadable"] and result["patchState"] != "not_checked"
print(json.dumps(result, sort_keys=True))
PY
