#!/bin/zsh
set -euo pipefail
project_dir=${0:A:h:h}

cd "$project_dir/services/bridge"
uv run pytest
uv run ruff check src tests
cd "$project_dir"
npm run build
npm test
cd "$project_dir/apps/android"
JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home" ANDROID_HOME="$HOME/Library/Android/sdk" ./gradlew assembleDebug testDebugUnitTest
git apply --check "$project_dir/patches/hermes-session-run-control.patch" --directory=/path/to/hermes-agent || true

test -s "$project_dir/apps/hub/HermesG2.ehpk"
test -s "$project_dir/apps/android/app/build/outputs/apk/debug/app-debug.apk"
print "Local release artifacts verified. Physical G2, Doze, reboot and Mac-mini power-cycle acceptance remain deployment tests."

