
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


@pytest.mark.asyncio
async def test_attachment_claim_is_atomic_device_and_session_bound(store, tmp_path):
    path = tmp_path / "private-upload.pdf"
    path.write_bytes(b"document")
    await store.record_attachment(
        "attachment-1",
        "device-a",
        "session-a",
        "report.pdf",
        "application/pdf",
        path,
        "digest",
        8,
    )

    with pytest.raises(ValueError, match="not available"):
        await store.claim_attachments("device-b", "session-a", ["attachment-1"])
    with pytest.raises(ValueError, match="not available"):
        await store.claim_attachments("device-a", "session-b", ["attachment-1"])

    claimed = await store.claim_attachments("device-a", "session-a", ["attachment-1"])
    assert claimed == [{
        "attachmentId": "attachment-1",
        "name": "report.pdf",
        "mediaType": "application/pdf",
        "path": str(path),
        "sha256": "digest",
        "size": 8,
    }]

    with pytest.raises(ValueError, match="not available"):
        await store.claim_attachments("device-a", "session-a", ["attachment-1"])


async def test_migrations_are_idempotent(store):
    await store.migrate()
    async with store.connect() as database:
        versions = await database.execute_fetchall(
            "SELECT version FROM schema_migrations ORDER BY version"
        )
    assert [row["version"] for row in versions] == list(range(1, len(versions) + 1))


async def test_migration_recovers_when_column_committed_before_marker(tmp_path):
    store = Store(tmp_path / "bridge.db")
    await store.migrate()
    async with store.connect() as database:
        await database.execute("DELETE FROM schema_migrations WHERE version=2")
        await database.commit()

    await store.migrate()

    async with store.connect() as database:
        versions = await database.execute_fetchall(
            "SELECT version FROM schema_migrations ORDER BY version"
        )
        columns = await database.execute_fetchall("PRAGMA table_info(session_state)")
    assert [row["version"] for row in versions] == [1, 2, 3]
    assert "source_override" in {row["name"] for row in columns}


async def test_events_replay_in_cursor_order(store):
    first = await store.append_event(EventInput(kind="run.started", source="hermes", sessionId="s", runId="r", payload={}))
    second = await store.append_event(EventInput(kind="run.completed", source="hermes", sessionId="s", runId="r", payload={}))
    replay = await store.events_after(first["cursor"] - 1)
    assert [item["eventId"] for item in replay] == [first["eventId"], second["eventId"]]


async def test_idempotency_rejects_body_mismatch(store):
    fresh, _, _ = await store.idempotency_begin("d", "same-key", b"one")
    assert fresh
    await store.idempotency_finish("d", "same-key", 200, {"ok": True})
    fresh, status, cached = await store.idempotency_begin("d", "same-key", b"one")
    assert not fresh and status == 200 and cached == {"ok": True}
    with pytest.raises(ValueError):
        await store.idempotency_begin("d", "same-key", b"two")


async def test_prompt_queue_survives_store_reopen(store):
    assert await store.enqueue_prompt("session", {"text": "first"}) == 1
    assert await store.enqueue_prompt("session", {"text": "second"}) == 2
    reopened = Store(store.path)
    assert await reopened.dequeue_prompt("session") == {"text": "first"}
    assert await reopened.dequeue_prompt("session") == {"text": "second"}
    assert await reopened.dequeue_prompt("session") is None


async def test_approval_pending_requires_exact_session_run_and_request(store):
    await store.append_event(EventInput(
        kind="approval.required",
        source="hermes",
        sessionId="session-a",
        runId="run-a",
        payload={"requestId": "request-a"},
    ))
    assert await store.approval_is_pending("session-a", "run-a", "request-a")
    assert not await store.approval_is_pending("session-b", "run-a", "request-a")
    assert not await store.approval_is_pending("session-a", "run-b", "request-a")
    assert not await store.approval_is_pending("session-a", "run-a", "request-b")
    await store.append_event(EventInput(
        kind="approval.resolved",
        source="bridge",
        sessionId="session-a",
        runId="run-a",
        payload={"requestId": "request-a", "choice": "deny"},
    ))
    assert not await store.approval_is_pending("session-a", "run-a", "request-a")


async def test_session_overlays_persist_pin_queue_and_external_busy_state(store):
    async with store.connect() as database:
        await database.execute(
            "INSERT INTO session_state(session_id,pinned,queued_prompts_json,updated_at) VALUES(?,?,?,?)",
            ("pinned", 1, "[]", "now"),
        )
        await database.commit()
    await store.append_event(EventInput(
        kind="run.started", source="plugin", sessionId="busy", runId="turn", payload={}
    ))
    await store.enqueue_prompt("queued", {"text": "later"})
    overlays = await store.session_overlays(["pinned", "busy", "queued"])
    assert overlays["pinned"]["pinned"] is True
    assert overlays["busy"]["state"] == "busy"
    assert overlays["queued"]["state"] == "queued"
    await store.append_event(EventInput(
        kind="run.completed", source="plugin", sessionId="busy", runId="turn", payload={}
    ))
    assert not await store.session_is_busy("busy")
    await store.append_event(EventInput(
        kind="message.completed",
        source="hermes",
        sessionId="busy",
        runId="turn",
        payload={"content": "Durable final answer"},
    ))
    assert (await store.session_overlays(["busy"]))["busy"]["latestAnswer"] == (
        "Durable final answer"
    )


async def test_managed_run_correlation_tracks_owner_and_terminal_state(store):
    await store.update_run("run", "session", "device", "started", True)
    async with store.connect() as database:
        running = await (await database.execute(
            "SELECT * FROM run_correlation WHERE run_id='run'"
        )).fetchone()
    assert running["session_id"] == "session"
    assert running["initiated_by_g2"] == 1
    assert running["status"] == "started"
    await store.update_run("run", "session", "device", "completed", True)
    async with store.connect() as database:
        session = await (await database.execute(
            "SELECT active_run_id FROM session_state WHERE session_id='session'"
        )).fetchone()
    assert session["active_run_id"] is None


async def test_session_source_override_survives_refresh(store):
    await store.set_session_source("session", "even_g2")
    overlay = (await store.session_overlays(["session"]))["session"]
    assert overlay["source"] == "even_g2"
