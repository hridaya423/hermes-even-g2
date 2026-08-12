#!/bin/zsh
set -euo pipefail
project_dir=${0:A:h:h}
artifact_dir=${HERMES_RELEASE_DIR:-$project_dir/release}
run_started=$(date +%s)

cd "$project_dir"
PYTHONPATH=packages/hermes-plugin services/bridge/.venv/bin/ruff check packages/hermes-plugin services/bridge
PYTHONPATH=packages/hermes-plugin services/bridge/.venv/bin/pytest packages/hermes-plugin/tests services/bridge/tests
npm run build -w @hermes-g2/protocol
npm run typecheck
npm run build
npm test
cd "$project_dir/apps/android"
JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home" ANDROID_HOME="$HOME/Library/Android/sdk" ./gradlew --no-daemon --rerun-tasks assembleDebug testDebugUnitTest
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
for artifact in "$project_dir/apps/hub/HermesG2.ehpk" "$project_dir/apps/android/app/build/outputs/apk/debug/app-debug.apk"; do
  artifact_mtime=$(stat -f %m "$artifact" 2>/dev/null || stat -c %Y "$artifact")
  if (( artifact_mtime < run_started )); then
    print -u2 "Release artifact was not rebuilt during this verification run: $artifact"
    exit 1
  fi
done

if rg -n --hidden --glob '!node_modules/**' --glob '!apps/hub/dist/**' --glob '!apps/android/.gradle/**' --glob '!release/**' '(REPLACE_ME|replace-me|YOUR_[A-Z0-9_]*(TOKEN|KEY|SECRET)|<your-[^>]+>)' "$project_dir/apps/hub/HermesG2.ehpk" "$project_dir/apps/android/app/build/outputs/apk/debug/app-debug.apk" >/dev/null 2>&1; then
  print -u2 "Generated release artifacts contain a placeholder credential."
  exit 1
fi

mkdir -p "$artifact_dir"
cp "$project_dir/apps/hub/HermesG2.ehpk" "$artifact_dir/HermesG2.ehpk"
cp "$project_dir/apps/android/app/build/outputs/apk/debug/app-debug.apk" "$artifact_dir/hermes-g2-debug.apk"
python3 "$project_dir/scripts/write-release-manifest.py" "$artifact_dir/manifest.json" hermes-even-g2 \
  HermesG2.ehpk "$artifact_dir/HermesG2.ehpk" \
  hermes-g2-debug.apk "$artifact_dir/hermes-g2-debug.apk"
shasum -a 256 "$artifact_dir/HermesG2.ehpk" "$artifact_dir/hermes-g2-debug.apk" > "$artifact_dir/SHA256SUMS"
print "Local release artifacts and SHA-256 manifest written to $artifact_dir. Physical G2, Doze, reboot and Mac-mini power-cycle acceptance remain deployment tests."
