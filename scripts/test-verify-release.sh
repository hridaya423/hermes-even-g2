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
