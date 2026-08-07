import hashlib
import os
from typing import Any

import httpx


class HermesG2Observer:
    """Fail-open Hermes hook observer. It never executes actions or delays Hermes."""

    def __init__(self) -> None:
        self.origin = os.environ.get("HERMES_G2_PLUGIN_ORIGIN", "http://127.0.0.1:8765")
        self.secret = os.environ.get("HERMES_G2_PLUGIN_SECRET", "")

    async def _send(self, kind: str, kwargs: dict[str, Any]) -> None:
        if not self.secret:
            return
        session_id = str(kwargs.get("session_id") or kwargs.get("sessionId") or "") or None
        run_id = str(kwargs.get("run_id") or kwargs.get("runId") or "") or None
        safe = {
            "hook": kind,
            "tool": kwargs.get("tool_name") or kwargs.get("tool"),
            "status": kwargs.get("status"),
            "source": kwargs.get("source"),
            "errorType": type(kwargs["error"]).__name__ if kwargs.get("error") else None,
        }
        try:
            async with httpx.AsyncClient(timeout=0.75) as client:
                await client.post(
                    f"{self.origin}/internal/plugin/events",
                    headers={"X-Plugin-Secret": self.secret},
                    json={"kind": self._event_kind(kind), "source": "plugin", "sessionId": session_id, "runId": run_id, "payload": safe},
                )
        except Exception:
            return

    @staticmethod
    def _event_kind(hook: str) -> str:
        return {
            "on_session_start": "session.updated", "on_session_end": "session.updated",
            "on_session_finalize": "message.completed", "on_session_reset": "session.updated",
            "pre_tool_call": "tool.started", "post_tool_call": "tool.completed",
            "post_llm_call": "run.progress", "subagent_stop": "subagent.completed",
        }[hook]

    async def on_session_start(self, **kwargs): await self._send("on_session_start", kwargs)
    async def on_session_end(self, **kwargs): await self._send("on_session_end", kwargs)
    async def on_session_finalize(self, **kwargs): await self._send("on_session_finalize", kwargs)
    async def on_session_reset(self, **kwargs): await self._send("on_session_reset", kwargs)
    async def pre_tool_call(self, **kwargs): await self._send("pre_tool_call", kwargs)
    async def post_tool_call(self, **kwargs): await self._send("post_tool_call", kwargs)
    async def post_llm_call(self, **kwargs): await self._send("post_llm_call", kwargs)
    async def subagent_stop(self, **kwargs): await self._send("subagent_stop", kwargs)

