#!/bin/zsh
set -euo pipefail

project_dir=${0:A:h:h}
release_script="$project_dir/scripts/verify-release.sh"
protocol_build_line=$(awk '$0 == "npm run build -w @hermes-g2/protocol" { print NR; exit }' "$release_script")
typecheck_line=$(awk '$0 == "npm run typecheck" { print NR; exit }' "$release_script")

if [[ -z $protocol_build_line || -z $typecheck_line ]]; then
  print -u2 "The release gate must explicitly build the protocol and run typecheck."
  exit 1
fi

if (( protocol_build_line >= typecheck_line )); then
  print -u2 "The protocol package must be built before the Hub typecheck."
  exit 1
fi

for required in "run_started" "HERMES_RELEASE_DIR" "SHA256SUMS" "write-release-manifest.py" "artifact_mtime" "placeholder credential"; do
  rg -F "$required" "$release_script" >/dev/null || { print -u2 "Release gate is missing: $required"; exit 1; }
done
zsh -n "$release_script" "$project_dir/scripts/test-verify-release.sh" "$project_dir/scripts/audit-mac-mini.sh"
python3 -c 'from pathlib import Path; path = Path(__import__("sys").argv[1]); compile(path.read_text(), str(path), "exec")' "$project_dir/scripts/write-release-manifest.py"
