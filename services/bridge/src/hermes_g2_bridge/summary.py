import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .store import Store

SUMMARY_KEYS = {
    "headline", "outcome", "keyChanges", "validation", "blocker", "suggestedNextAction",
}


def deterministic_summary(content: str) -> dict[str, str]:
    lines = [re.sub(r"^[#>*\-\d.\s]+", "", line).strip() for line in content.splitlines()]
    meaningful = [line for line in lines if len(line) >= 8]
    sentences = re.split(r"(?<=[.!?])\s+", " ".join(meaningful))
    sentences = [sentence.strip() for sentence in sentences if sentence.strip()]
    first = (sentences[0] if sentences else "Agent response completed").strip()
    validation = next(
        (sentence for sentence in sentences if re.search(r"\b(test|tests|passed|verified|build|lint)\b", sentence, re.IGNORECASE)),
        "Not verified",
    )
    blocker = next(
        (sentence for sentence in sentences if re.search(r"\b(blocked|blocker|failed|cannot|can't|error)\b", sentence, re.IGNORECASE)),
        "None",
    )
    next_action = next(
        (sentence for sentence in sentences if re.search(r"\b(next|then|remaining|follow[- ]?up)\b", sentence, re.IGNORECASE)),
        "Continue in this session if another change is needed.",
    )
    return {
        "headline": first[:120],
        "outcome": " ".join(sentences[:2])[:360] or first[:360],
        "keyChanges": " ".join(sentences[1:3])[:300] or first[:300],
        "validation": validation[:240],
        "blocker": blocker[:240],
        "suggestedNextAction": next_action[:240],
    }


async def summarize(content: str, helper: Path, store: Store) -> dict[str, Any]:
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    cached = await store.cached_summary(content_hash)
    if cached:
        return cached
    baseline = deterministic_summary(content)
    result: dict[str, Any] | None = None
    if helper.exists() and helper.is_file():
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                str(helper),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(
                process.communicate(content.encode()),
                timeout=8,
            )
            candidate = json.loads(stdout)
            if process.returncode == 0 and SUMMARY_KEYS <= candidate.keys():
                if baseline["validation"] != "Not verified":
                    candidate["validation"] = baseline["validation"]
                if baseline["blocker"] != "None":
                    candidate["blocker"] = baseline["blocker"]
                result = candidate
        except (OSError, ValueError, TimeoutError, json.JSONDecodeError):
            if process is not None and process.returncode is None:
                process.kill()
    result = result or baseline
    await store.cache_summary(content_hash, result)
    return result
