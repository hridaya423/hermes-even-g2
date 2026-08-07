from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from hermes_g2_bridge.models import AgentAction


def action(**overrides):
    value = {"kind": "prompt", "deviceId": "device", "idempotencyKey": "12345678", "sessionId": "session", "createdAt": datetime.now(UTC).isoformat(), "payload": {"text": "continue"}}
    value.update(overrides)
    return value


def test_prompt_requires_session():
    with pytest.raises(ValidationError):
        AgentAction.model_validate(action(sessionId=None))


def test_run_controls_require_session_and_run():
    with pytest.raises(ValidationError):
        AgentAction.model_validate(action(kind="stopRun", runId=None))


def test_rename_requires_exact_session():
    with pytest.raises(ValidationError):
        AgentAction.model_validate(action(kind="renameSession", sessionId=None))


def test_model_selection_requires_exact_session():
    with pytest.raises(ValidationError):
        AgentAction.model_validate(action(kind="setSessionModel", sessionId=None))
