#!/bin/zsh
set -euo pipefail
project_dir=${0:A:h:h}

cd "$project_dir"
PYTHONPATH=packages/hermes-plugin services/bridge/.venv/bin/ruff check packages/hermes-plugin services/bridge
PYTHONPATH=packages/hermes-plugin services/bridge/.venv/bin/pytest packages/hermes-plugin/tests services/bridge/tests
npm run build -w @hermes-g2/protocol
npm run typecheck
npm run build
npm test
cd "$project_dir/apps/android"
JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home" ANDROID_HOME="$HOME/Library/Android/sdk" ./gradlew --no-daemon assembleDebug testDebugUnitTest
swift build -c release --package-path "$project_dir/services/apple-summary-helper"
zsh -n "$project_dir"/scripts/*.sh
zsh "$project_dir/scripts/test-verify-release.sh"
zsh "$project_dir/scripts/test-stage-mac-mini-user.sh"
git -C "$project_dir" diff --check

if [[ -n ${HERMES_AGENT_CHECKOUT:-} && -d ${HERMES_AGENT_CHECKOUT}/.git ]]; then
  if ! git -C "$HERMES_AGENT_CHECKOUT" apply --check "$project_dir/patches/hermes-session-run-control.patch" 2>/dev/null; then
    git -C "$HERMES_AGENT_CHECKOUT" apply --reverse --check "$project_dir/patches/hermes-session-run-control.patch"
  fi
elif [[ -n ${HERMES_AGENT_CHECKOUT:-} ]]; then
  print "Hermes compatibility checkout is absent; skipped external patch-state check."
fi

test -s "$project_dir/apps/hub/HermesG2.ehpk"
test -s "$project_dir/apps/android/app/build/outputs/apk/debug/app-debug.apk"
print "Local release artifacts verified. Physical G2, Doze, reboot and Mac-mini power-cycle acceptance remain deployment tests."
