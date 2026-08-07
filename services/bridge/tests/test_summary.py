from pathlib import Path

from hermes_g2_bridge.store import Store
from hermes_g2_bridge.summary import deterministic_summary, summarize


def test_deterministic_summary_preserves_validation_and_blocker():
    value = deterministic_summary(
        "Implemented durable replay. Tests passed: 15. Remaining blocker: admin install is required."
    )
    assert "Implemented durable replay" in value["headline"]
    assert "Tests passed" in value["validation"]
    assert "blocker" in value["blocker"]


async def test_summary_fallback_is_cached(tmp_path):
    store = Store(tmp_path / "bridge.db")
    await store.migrate()
    first = await summarize("Built the bridge. Tests passed.", Path("/missing/helper"), store)
    second = await summarize("Built the bridge. Tests passed.", Path("/missing/helper"), store)
    assert first == second
    async with store.connect() as database:
        count = await (await database.execute("SELECT COUNT(*) FROM summary_cache")).fetchone()
    assert count[0] == 1


async def test_model_summary_cannot_erase_explicit_validation(tmp_path):
    helper = tmp_path / "summary-helper"
    helper.write_text("#!/bin/sh\necho '{\"headline\":\"Done\",\"outcome\":\"Done\",\"keyChanges\":\"Changed\",\"validation\":\"Not verified\",\"blocker\":\"None\",\"suggestedNextAction\":\"Continue\"}'\n")
    helper.chmod(0o755)
    store = Store(tmp_path / "bridge.db")
    await store.migrate()
    result = await summarize("Implemented replay. All 18 tests passed.", helper, store)
    assert result["validation"] == "All 18 tests passed."
