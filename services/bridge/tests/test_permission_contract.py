from datetime import UTC, datetime
from pathlib import Path

import pytest

from hermes_g2_bridge.models import AgentAction, EventInput
from hermes_g2_bridge.service import APPROVAL_MAP, ControlService, sanitize_event_payload
from hermes_g2_bridge.store import Store


class ApprovalHermes:
    def __init__(self):
        self.approved: list[tuple[str, str, str]] = []

    async def approve(self, session_id: str, run_id: str, choice: str):
        self.approved.append((session_id, run_id, choice))
        return {"status": "accepted", "choice": choice}


async def configured(tmp_path: Path):
    store = Store(tmp_path / "bridge.db")
    await store.migrate()
    hermes = ApprovalHermes()
    service = ControlService(
        store,
        hermes,
        action_max_age_seconds=300,
        summary_helper=tmp_path / "summary-helper",
        whisper_binary=tmp_path / "whisper",
        whisper_model=tmp_path / "model",
        tailscale_cli=tmp_path / "tailscale",
    )
    service.runtime.update(coreReady=True, hermes=True)
    service.capabilities.update(
        nativeSessions=True,
        sessionHistory=True,
        sessionStreaming=True,
        sessionApprovalResponse=True,
    )
    return store, hermes, service


def approval_action(kind: str, session_id: str, run_id: str, request_id: str, key: str) -> AgentAction:
    return AgentAction(
        kind=kind,
        deviceId="device",
        idempotencyKey=key,
        sessionId=session_id,
        runId=run_id,
        createdAt=datetime.now(UTC),
        payload={"requestId": request_id},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "native"),
    [("approveOnce", "once"), ("approveSession", "session"), ("approveAlways", "always"), ("deny", "deny")],
)
async def test_every_hermes_approval_choice_reaches_native_session_run(tmp_path: Path, kind: str, native: str):
    store, hermes, service = await configured(tmp_path)
    session_id, run_id, request_id = "session", f"run-{kind}", f"request-{kind}"
    await store.append_event(EventInput(kind="approval.required", source="bridge", sessionId=session_id, runId=run_id, payload={"requestId": request_id, "tool": "shell", "choices": ["once", "session", "always", "deny"]}))

    response = await service.execute(
        approval_action(kind, session_id, run_id, request_id, f"action-{kind}"),
        {"id": "device", "scopes": ["approvals:write"]},
    )

    assert response == {"status": "accepted", "choice": native}
    assert hermes.approved == [(session_id, run_id, native)]


@pytest.mark.asyncio
async def test_stale_and_wrong_session_or_run_approvals_are_rejected_before_hermes(tmp_path: Path):
    store, hermes, service = await configured(tmp_path)
    await store.append_event(EventInput(kind="approval.required", source="bridge", sessionId="session-a", runId="run-a", payload={"requestId": "request-a"}))

    for index, action in enumerate(
        [
            approval_action("approveOnce", "session-b", "run-a", "request-a", "wrong-session"),
            approval_action("approveOnce", "session-a", "run-b", "request-a", "wrong-run"),
        ],
    ):
        with pytest.raises(ValueError, match="stale|does not match"):
            await service.execute(action, {"id": "device", "scopes": ["approvals:write"]})

    valid = approval_action("approveOnce", "session-a", "run-a", "request-a", "valid-action")
    await service.execute(valid, {"id": "device", "scopes": ["approvals:write"]})
    with pytest.raises(ValueError, match="stale|does not match"):
        await service.execute(approval_action("approveOnce", "session-a", "run-a", "request-a", "replay-action"), {"id": "device", "scopes": ["approvals:write"]})

    assert hermes.approved == [("session-a", "run-a", "once")]


@pytest.mark.parametrize("kind", list(APPROVAL_MAP))
def test_approval_actions_require_exact_session_and_run(kind):
    with pytest.raises(ValueError):
        AgentAction(
            kind=kind,
            deviceId="device",
            idempotencyKey=f"missing-{kind}",
            sessionId="session",
            createdAt=datetime.now(UTC),
            payload={},
        )


def test_sensitive_approval_payloads_are_redacted_before_event_persistence():
    payload = sanitize_event_payload(
        "approval.required",
        {
            "requestId": "request-1",
            "command": "curl -H 'Authorization: Bearer super-secret-value' https://example.test",
            "token": "super-secret-value",
            "password": "super-secret-value",
            "environment": {"API_TOKEN": "super-secret-value"},
            "tool": "shell",
        },
    )

    encoded = str(payload)
    assert "super-secret-value" not in encoded
    assert "Bearer <redacted>" in payload["command"]
    assert payload["token"] == "<redacted>"
    assert payload["password"] == "<redacted>"
    assert payload["environment"] == "<redacted>"
