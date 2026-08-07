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

install -d -m 0755 "$install_root" "$state_root" "$state_root/models" "$log_root"
install -d -m 0700 "$config_root"
if [[ ! -f "$config_root/bridge.env" ]]; then
  install -m 0600 "$project_dir/deploy/hermes-g2.env.example" "$config_root/bridge.env"
  print -u2 "Edit $config_root/bridge.env with generated secrets, then rerun."
  exit 2
fi
chmod 0600 "$config_root/bridge.env"
rsync -a --delete "$project_dir/services/bridge/" "$install_root/bridge/"
python3.11 -m venv "$install_root/venv"
"$install_root/venv/bin/pip" install --disable-pip-version-check "$install_root/bridge"
install -m 0644 "$project_dir/deploy/launchd/com.honey.hermes-g2.bridge.plist" /Library/LaunchDaemons/com.honey.hermes-g2.bridge.plist
launchctl bootout system/com.honey.hermes-g2.bridge 2>/dev/null || true
launchctl bootstrap system /Library/LaunchDaemons/com.honey.hermes-g2.bridge.plist
launchctl enable system/com.honey.hermes-g2.bridge
print "Bridge installed. Configure Tailscale Serve with scripts/configure-tailscale.sh."

