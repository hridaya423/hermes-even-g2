#!/bin/zsh
set -euo pipefail

if [[ ${EUID} -eq 0 ]]; then
  model_root=/var/lib/hermes-g2/models
else
  model_root=${HERMES_HOME:-$HOME/.hermes}/hermes-g2/state/models
fi
model_path=$model_root/ggml-tiny.en-q5_1.bin
download_path=$model_path.download
expected_sha256=c77c5766f1cef09b6b7d47f21b546cbddd4157886b3b5d6d4f709e91e66c7c2b

mkdir -p "$model_root"
curl -fL --retry 3 \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.en-q5_1.bin \
  -o "$download_path"
actual_sha256=$(shasum -a 256 "$download_path" | cut -d' ' -f1)
if [[ $actual_sha256 != $expected_sha256 ]]; then
  print -u2 "Whisper model checksum mismatch."
  exit 3
fi
mv "$download_path" "$model_path"
chmod 0644 "$model_path"
print "Installed checksum-verified Whisper model at $model_path"
