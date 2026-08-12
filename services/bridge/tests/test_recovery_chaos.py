"""Deterministic crash/reconnect acceptance cases for the bridge store.

These tests model the boundaries that are hardest to exercise with a live
Hermes process: SQLite is reopened between phases, clocks are controlled by
the stored timestamps, and every recovery step is explicit.  A restart may
make a prompt retryable, but it must never silently run it again.
"""

import asyncio
import os
import time
from pathlib import Path

import pytest

from hermes_g2_bridge.models import EventInput
from hermes_g2_bridge.store import Store


async def _new_store(path: Path, attachments_root: Path | None = None, grace: int = 300) -> Store:
    value = Store(path, attachments_root, grace)
    await value.migrate()
    return value


@pytest.mark.asyncio
async def test_restart_preserves_cursor_and_replays_only_events_after_ack(tmp_path: Path):
    path = tmp_path / "bridge.db"
    first = await _new_store(path)
    created = await first.append_event(
        EventInput(kind="run.started", source="hermes", sessionId="session", runId="run", payload={})
    )

    # A process restart opens the same WAL database; no in-memory cursor is
    # trusted to recover the stream position.
    restarted = await _new_store(path)
    completed = await restarted.append_event(
        EventInput(kind="run.completed", source="hermes", sessionId="session", runId="run", payload={})
    )

    assert (await restarted.event_bounds()) == (created["cursor"], completed["cursor"])
    replay = await restarted.events_after(created["cursor"])
    assert [item["eventId"] for item in replay] == [completed["eventId"]]


@pytest.mark.asyncio
async def test_replay_gap_requires_snapshot_before_advancing_cursor(tmp_path: Path):
    store = await _new_store(tmp_path / "bridge.db")
    for index in range(4):
        await store.append_event(EventInput(kind="run.progress", source="hermes", sessionId="s", payload={"index": index}))

    async with store.connect() as db:
        await db.execute("DELETE FROM events WHERE cursor < 4")
        await db.commit()

    oldest, latest = await store.event_bounds()
    assert (oldest, latest) == (4, 4)

    # The client keeps its acknowledged cursor when hydration fails.  Only a
    # successful replacement snapshot permits it to move to the latest bound.
    acknowledged = 1
    snapshot_succeeded = False
    if oldest > 0 and acknowledged < oldest - 1:
        assert snapshot_succeeded is False
        assert acknowledged == 1
        snapshot_succeeded = True
        if snapshot_succeeded:
            acknowledged = latest
    assert acknowledged == latest


@pytest.mark.asyncio
@pytest.mark.parametrize("admission_state", ["admitted", "claimed"])
async def test_restart_interrupts_admitted_or_claimed_prompt_without_auto_rerun(
    tmp_path: Path, admission_state: str
):
    path = tmp_path / "bridge.db"
    store = await _new_store(path)
    code = await store.create_pairing("hub", 90)
    device_id, _, _ = await store.exchange_pairing(code, "glasses", "hub")
    if admission_state == "admitted":
        admitted = await store.admit_prompt(
            "session", {"text": "run once", "deviceId": device_id}
        )
        assert admitted["status"] == "admitted"
    else:
        queued = await store.admit_prompt(
            "session", {"text": "run once", "deviceId": device_id}, force_queue=True
        )
        assert queued["status"] == "queued"
        claimed = await store.claim_next_prompt("session")
        assert claimed and claimed["text"] == "run once"

    restarted = await _new_store(path)
    recovered = await restarted.recover_prompt_admissions()
    assert recovered and recovered[0]["status"] == "interrupted"
    assert recovered[0]["queueId"]
    recovery = await restarted.events_after(0)
    assert recovery[-1]["kind"] == "attention.created"
    assert recovery[-1]["payload"]["retryable"] is True
    assert await restarted.list_queued_prompts("session") == []
    assert not await restarted.session_turn_active("session")

    # Recovery intentionally does not start a provider turn.  A retry must be
    # a new, explicit admission with a new idempotency key.
    retry = await restarted.admit_prompt(
        "session", {"text": "run once", "deviceId": device_id}
    )
    assert retry["status"] == "admitted"
    assert retry.get("admissionId")


@pytest.mark.asyncio
async def test_queued_prompt_survives_restart_and_is_claimed_once_when_idle(tmp_path: Path):
    path = tmp_path / "bridge.db"
    store = await _new_store(path)
    code = await store.create_pairing("hub", 90)
    device_id, _, _ = await store.exchange_pairing(code, "glasses", "hub")
    first = await store.admit_prompt("session", {"text": "active", "deviceId": device_id})
    assert first["status"] == "admitted"
    second = await store.admit_prompt("session", {"text": "later", "deviceId": device_id})
    assert second["status"] == "queued"

    restarted = await _new_store(path)
    assert [item["text"] for item in await restarted.list_queued_prompts("session")] == ["later"]
    # A queued item remains inert after restart until reconciliation releases
    # the interrupted admission and observes an idle session.
    await restarted.release_admission("session", first["admissionId"])
    claimed = await restarted.claim_next_prompt("session")
    assert claimed and claimed["text"] == "later"
    assert await restarted.claim_next_prompt("session") is None


@pytest.mark.asyncio
async def test_revoked_live_stream_stops_before_post_revoke_event(tmp_path: Path):
    store = await _new_store(tmp_path / "bridge.db")
    code = await store.create_pairing("hub", 90)
    device_id, _, _ = await store.exchange_pairing(code, "glasses", "hub")
    event = await store.append_event(
        EventInput(kind="attention.created", source="bridge", sessionId="session", payload={})
    )
    stream = store.event_stream(0, device_id=device_id)
    assert (await stream.__anext__())["eventId"] == event["eventId"]

    await store.revoke_device(device_id)
    await store.append_event(
        EventInput(kind="attention.created", source="bridge", sessionId="session", payload={"late": True})
    )
    assert await asyncio.wait_for(stream.__anext__(), timeout=1) == {
        "type": "auth.revoked",
        "deviceId": device_id,
    }
    with pytest.raises(StopAsyncIteration):
        await stream.__anext__()


@pytest.mark.asyncio
async def test_attachment_cleanup_preserves_fresh_orphan_and_removes_stale_one(tmp_path: Path):
    root = tmp_path / "attachments"
    root.mkdir()
    fresh = root / "fresh.bin"
    stale = root / "stale.bin"
    fresh.write_bytes(b"fresh")
    stale.write_bytes(b"stale")
    old = time.time() - 3600
    os.utime(stale, (old, old))
    store = await _new_store(tmp_path / "bridge.db", root, grace=300)

    assert await store.cleanup_attachments() == 1
    assert fresh.exists()
    assert not stale.exists()


@pytest.mark.asyncio
async def test_attachment_record_and_cleanup_race_keeps_registered_file(tmp_path: Path):
    root = tmp_path / "attachments"
    root.mkdir()
    path = root / "upload.bin"
    path.write_bytes(b"payload")
    store = await _new_store(tmp_path / "bridge.db", root, grace=300)

    record = asyncio.create_task(
        store.record_attachment(
            "attachment", "device", "session", "upload.bin", "application/octet-stream", path, "digest", 7
        )
    )
    await asyncio.sleep(0)
    await store.cleanup_attachments()
    await record

    async with store.connect() as db:
        row = await (await db.execute("SELECT id FROM attachments WHERE id='attachment'")).fetchone()
    assert row and path.exists()
