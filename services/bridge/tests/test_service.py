import base64
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from hermes_g2_bridge.models import AgentAction
from hermes_g2_bridge.service import ControlService
from hermes_g2_bridge.store import Store


class RecoveringHermes:
    def __init__(self):
        self.probe_calls = 0

    async def probe(self):
        self.probe_calls += 1
        return {
            "healthy": True,
            "detailed": {"gui_ready": False},
            "nativeSessions": True,
            "sessionHistory": True,
            "sessionStreaming": True,
            "sessionRunControl": True,
            "sessionApprovalResponse": True,
        }

    async def stop_run(self, session_id: str, run_id: str):
        self.stopped = (session_id, run_id)
        return {"status": "stopping"}


@pytest.mark.asyncio
async def test_ensure_core_reprobes_after_hermes_restart(tmp_path: Path):
    hermes = RecoveringHermes()
    service = ControlService(
        Store(tmp_path / "bridge.db"),
        hermes,
        action_max_age_seconds=300,
        summary_helper=tmp_path / "summary-helper",
        whisper_binary=tmp_path / "whisper",
        whisper_model=tmp_path / "model",
        tailscale_cli=tmp_path / "tailscale",
    )
    service.runtime["coreReady"] = False

    await service.ensure_core()

    assert hermes.probe_calls == 1
    assert service.runtime["coreReady"] is True


def stop_action(session_id: str, run_id: str) -> AgentAction:
    return AgentAction(
        kind="stopRun",
        deviceId="device",
        idempotencyKey=f"stop-{session_id}-{run_id}",
        sessionId=session_id,
        runId=run_id,
        expectedState="running",
        createdAt=datetime.now(UTC),
    )


async def configured_control(tmp_path: Path):
    store = Store(tmp_path / "bridge.db")
    await store.migrate()
    hermes = RecoveringHermes()
    service = ControlService(
        store,
        hermes,
        action_max_age_seconds=300,
        summary_helper=tmp_path / "summary-helper",
        whisper_binary=tmp_path / "whisper",
        whisper_model=tmp_path / "model",
        tailscale_cli=tmp_path / "tailscale",
    )
    service.runtime["coreReady"] = True
    service.capabilities["sessionRunControl"] = True
    return store, hermes, service


@pytest.mark.asyncio
async def test_stop_run_rejects_a_run_owned_by_another_session(tmp_path: Path):
    store, hermes, service = await configured_control(tmp_path)
    await store.update_run("run-1", "session-a", "device", "started", True)

    with pytest.raises(ValueError, match="active run does not match"):
        await service.execute(
            stop_action("session-b", "run-1"),
            {"id": "device", "scopes": ["runs:control"]},
        )

    assert not hasattr(hermes, "stopped")


@pytest.mark.asyncio
async def test_stop_run_rejects_a_terminal_run(tmp_path: Path):
    store, hermes, service = await configured_control(tmp_path)
    await store.update_run("run-1", "session-a", "device", "cancelled", True)

    with pytest.raises(ValueError, match="active run does not match"):
        await service.execute(
            stop_action("session-a", "run-1"),
            {"id": "device", "scopes": ["runs:control"]},
        )

    assert not hasattr(hermes, "stopped")


@pytest.mark.asyncio
async def test_stop_run_routes_to_the_exact_active_session_and_run(tmp_path: Path):
    store, hermes, service = await configured_control(tmp_path)
    await store.update_run("run-1", "session-a", "device", "started", True)

    response = await service.execute(
        stop_action("session-a", "run-1"),
        {"id": "device", "scopes": ["runs:control"]},
    )

    assert response == {"status": "stopping"}
    assert hermes.stopped == ("session-a", "run-1")


@pytest.mark.asyncio
async def test_prepare_prompt_claims_exact_attachments_and_builds_native_image_input(tmp_path: Path):
    store, _, service = await configured_control(tmp_path)
    image = tmp_path / "image.png"
    image.write_bytes(b"image-bytes")
    document = tmp_path / "notes.pdf"
    document.write_bytes(b"document-bytes")
    await store.record_attachment("image-1", "device", "session-a", "image.png", "image/png", image, "a", 11)
    await store.record_attachment("doc-1", "device", "session-a", "notes.pdf", "application/pdf", document, "b", 14)

    content = await service.prepare_prompt(
        "session-a",
        "device",
        "Summarise these",
        ["image-1", "doc-1"],
    )

    assert content == [
        {
            "type": "text",
            "text": f"Summarise these\n\nAttached files staged on this Mac for this session:\n- notes.pdf: {document}",
        },
        {
            "type": "image_url",
            "image_url": {
                "url": "data:image/png;base64," + base64.b64encode(b"image-bytes").decode(),
                "detail": "auto",
            },
        },
    ]

    with pytest.raises(ValueError, match="not available"):
        await service.prepare_prompt("session-b", "device", "wrong session", ["image-1"])


@pytest.mark.asyncio
async def test_queued_prompt_defers_attachment_claim_until_run_start(tmp_path: Path):
    store, _, service = await configured_control(tmp_path)
    attachment = tmp_path / "queued.pdf"
    attachment.write_bytes(b"queued-document")
    await store.record_attachment(
        "queued-1", "device", "session-a", "queued.pdf", "application/pdf",
        attachment, "digest", len(b"queued-document"),
    )
    service.sessions = AsyncMock(return_value=[{"id": "session-a", "state": "busy"}])
    action = AgentAction(
        kind="queuePrompt",
        deviceId="device",
        idempotencyKey="queue-with-file",
        sessionId="session-a",
        createdAt=datetime.now(UTC),
        payload={"text": "Use this later", "attachmentIds": ["queued-1"]},
    )

    response = await service.execute(
        action,
        {"id": "device", "scopes": ["sessions:write"]},
    )

    assert response["status"] == "queued"
    queued = await store.dequeue_prompt("session-a")
    assert queued["attachmentIds"] == ["queued-1"]
    async with store.connect() as database:
        row = await (await database.execute(
            "SELECT consumed_at FROM attachments WHERE id='queued-1'"
        )).fetchone()
    assert row["consumed_at"] is None
    assert attachment.exists()
