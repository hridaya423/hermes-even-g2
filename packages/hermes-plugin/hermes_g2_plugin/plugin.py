import os
from typing import Any

import httpx


class HermesG2Observer:
    """Fail-open Hermes hook observer. It never executes actions or delays Hermes."""

    def __init__(self) -> None:
        self.origin = os.environ.get("HERMES_G2_PLUGIN_ORIGIN", "http://127.0.0.1:8765")
        self.secret = os.environ.get("HERMES_G2_PLUGIN_SECRET", "")

    def _send(self, kind: str, kwargs: dict[str, Any]) -> None:
        if not self.secret:
            return
        session_id = str(
            kwargs.get("session_id") or kwargs.get("sessionId") or kwargs.get("session_key") or ""
        ) or None
        run_id = str(
            kwargs.get("run_id") or kwargs.get("runId") or kwargs.get("turn_id") or ""
        ) or None
        safe = {
            "hook": kind,
            "tool": kwargs.get("tool_name") or kwargs.get("tool"),
            "status": kwargs.get("status"),
            "source": kwargs.get("source"),
            "choice": kwargs.get("choice"),
            "patternKey": kwargs.get("pattern_key"),
            "surface": kwargs.get("surface"),
            "errorType": type(kwargs["error"]).__name__ if kwargs.get("error") else None,
        }
        try:
            with httpx.Client(timeout=0.75) as client:
                client.post(
                    f"{self.origin}/internal/plugin/events",
                    headers={"X-Plugin-Secret": self.secret},
                    json={"kind": self._event_kind(kind), "source": "plugin", "sessionId": session_id, "runId": run_id, "payload": safe},
                )
        except Exception:  # noqa: BLE001 -- observation must never break an agent turn
            return

    @staticmethod
    def _event_kind(hook: str) -> str:
        return {
            "on_session_start": "session.updated", "on_session_end": "session.updated",
            "on_session_finalize": "message.completed", "on_session_reset": "session.updated",
            "pre_tool_call": "tool.started", "post_tool_call": "tool.completed",
            "pre_llm_call": "run.started", "post_llm_call": "run.completed",
            "subagent_start": "subagent.started",
            "subagent_stop": "subagent.completed", "pre_approval_request": "attention.created",
            "post_approval_response": "attention.resolved",
        }[hook]

    def on_session_start(self, **kwargs): self._send("on_session_start", kwargs)
    def on_session_end(self, **kwargs): self._send("on_session_end", kwargs)
    def on_session_finalize(self, **kwargs): self._send("on_session_finalize", kwargs)
    def on_session_reset(self, **kwargs): self._send("on_session_reset", kwargs)
    def pre_tool_call(self, **kwargs): self._send("pre_tool_call", kwargs)
    def post_tool_call(self, **kwargs): self._send("post_tool_call", kwargs)
    def pre_llm_call(self, **kwargs): self._send("pre_llm_call", kwargs)
    def post_llm_call(self, **kwargs): self._send("post_llm_call", kwargs)
    def subagent_start(self, **kwargs): self._send("subagent_start", kwargs)
    def subagent_stop(self, **kwargs): self._send("subagent_stop", kwargs)
    def pre_approval_request(self, **kwargs): self._send("pre_approval_request", kwargs)
    def post_approval_response(self, **kwargs): self._send("post_approval_response", kwargs)


def register(ctx) -> None:
    observer = HermesG2Observer()
    for hook_name in (
        "on_session_start",
        "on_session_end",
        "on_session_finalize",
        "on_session_reset",
        "pre_tool_call",
        "post_tool_call",
        "pre_llm_call",
        "post_llm_call",
        "subagent_start",
        "subagent_stop",
        "pre_approval_request",
        "post_approval_response",
    ):
        ctx.register_hook(hook_name, getattr(observer, hook_name))
