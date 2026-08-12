#!/usr/bin/env python3
"""Write a stable artifact manifest without embedding credentials or timestamps."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def main() -> int:
    if len(sys.argv) < 5 or len(sys.argv[3:]) % 2:
        print("usage: write-release-manifest.py OUTPUT PROJECT ARTIFACT_NAME PATH ...", file=sys.stderr)
        return 2
    output = Path(sys.argv[1]).resolve()
    project = sys.argv[2]
    values = sys.argv[3:]
    artifacts = []
    for index in range(0, len(values), 2):
        name, raw_path = values[index : index + 2]
        path = Path(raw_path).resolve()
        if not path.is_file() or path.stat().st_size == 0:
            print(f"missing or empty release artifact: {path}", file=sys.stderr)
            return 1
        artifacts.append({"name": name, "bytes": path.stat().st_size, "sha256": digest(path)})
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = {"schema": "private-glass-release/v1", "project": project, "artifacts": sorted(artifacts, key=lambda item: item["name"])}
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, output)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
