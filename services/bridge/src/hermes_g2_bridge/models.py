from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

PROTOCOL_VERSION = "1.0"


class ActionKind(StrEnum):
    CREATE_SESSION = "createSession"
    FORK_SESSION = "forkSession"
    RENAME_SESSION = "renameSession"
    PROMPT = "prompt"
    QUEUE_PROMPT = "queuePrompt"
    STOP_RUN = "stopRun"
    APPROVE_ONCE = "approveOnce"
    APPROVE_SESSION = "approveSession"
    APPROVE_ALWAYS = "approveAlways"
    DENY = "deny"
    PIN_SESSION = "pinSession"
    UNPIN_SESSION = "unpinSession"
    RUN_JOB = "runJob"
    PAUSE_JOB = "pauseJob"
    RESUME_JOB = "resumeJob"
    ACKNOWLEDGE = "acknowledge"


class AgentAction(BaseModel):
    kind: ActionKind
    device_id: str = Field(alias="deviceId", min_length=1)
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8)
    session_id: str | None = Field(default=None, alias="sessionId")
    run_id: str | None = Field(default=None, alias="runId")
    expected_state: str | None = Field(default=None, alias="expectedState")
    created_at: datetime = Field(alias="createdAt")
    payload: dict[str, Any] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def require_exact_targets(self):
        session_actions = {
            ActionKind.FORK_SESSION, ActionKind.RENAME_SESSION, ActionKind.PROMPT, ActionKind.QUEUE_PROMPT,
            ActionKind.PIN_SESSION, ActionKind.UNPIN_SESSION,
        }
        run_actions = {
            ActionKind.STOP_RUN, ActionKind.APPROVE_ONCE, ActionKind.APPROVE_SESSION,
            ActionKind.APPROVE_ALWAYS, ActionKind.DENY,
        }
        if self.kind in session_actions and not self.session_id:
            raise ValueError("action requires sessionId")
        if self.kind in run_actions and (not self.session_id or not self.run_id):
            raise ValueError("run action requires both sessionId and runId")
        return self


class PairingExchange(BaseModel):
    code: str = Field(min_length=6, max_length=12)
    device_name: str = Field(alias="deviceName", min_length=1, max_length=80)
    device_kind: Literal["hub", "android", "simulator"] = Field(alias="deviceKind")

    model_config = {"populate_by_name": True}


class EventInput(BaseModel):
    kind: str
    source: Literal["bridge", "hermes", "plugin"]
    session_id: str | None = Field(default=None, alias="sessionId")
    run_id: str | None = Field(default=None, alias="runId")
    payload: dict[str, Any] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


def utc_now() -> datetime:
    return datetime.now(UTC)
