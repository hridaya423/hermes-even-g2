
import pytest

from hermes_g2_bridge.models import EventInput
from hermes_g2_bridge.store import Store


@pytest.fixture
async def store(tmp_path):
    value = Store(tmp_path / "bridge.db")
    await value.migrate()
    return value


async def test_pairing_is_single_use_and_credentials_are_hashed(store):
    code = await store.create_pairing("hub", 90)
    device_id, credential, scopes = await store.exchange_pairing(code, "glasses", "hub")
    assert "sessions:write" in scopes
    assert await store.authenticate(device_id, credential)
    with pytest.raises(ValueError):
        await store.exchange_pairing(code, "other", "hub")
    async with store.connect() as db:
        row = await (await db.execute("SELECT credential_hash FROM devices WHERE id=?", (device_id,))).fetchone()
    assert credential not in row["credential_hash"]


async def test_events_replay_in_cursor_order(store):
    first = await store.append_event(EventInput(kind="run.started", source="hermes", sessionId="s", runId="r", payload={}))
    second = await store.append_event(EventInput(kind="run.completed", source="hermes", sessionId="s", runId="r", payload={}))
    replay = await store.events_after(first["cursor"] - 1)
    assert [item["eventId"] for item in replay] == [first["eventId"], second["eventId"]]


async def test_idempotency_rejects_body_mismatch(store):
    fresh, _ = await store.idempotency_begin("d", "same-key", b"one")
    assert fresh
    await store.idempotency_finish("d", "same-key", 200, {"ok": True})
    fresh, cached = await store.idempotency_begin("d", "same-key", b"one")
    assert not fresh and cached == {"ok": True}
    with pytest.raises(ValueError):
        await store.idempotency_begin("d", "same-key", b"two")


async def test_prompt_queue_survives_store_reopen(store):
    assert await store.enqueue_prompt("session", {"text": "first"}) == 1
    assert await store.enqueue_prompt("session", {"text": "second"}) == 2
    reopened = Store(store.path)
    assert await reopened.dequeue_prompt("session") == {"text": "first"}
    assert await reopened.dequeue_prompt("session") == {"text": "second"}
    assert await reopened.dequeue_prompt("session") is None
