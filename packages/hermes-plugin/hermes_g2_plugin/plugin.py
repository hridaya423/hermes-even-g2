import os
import queue
import threading
from collections.abc import Callable
from typing import Any

import httpx


class HermesG2Observer:
    """Fail-open Hermes hook observer. It never executes actions or delays Hermes."""

    def __init__(
        self,
        *,
        sender: Callable[[dict[str, Any]], None] | None = None,
        queue_size: int = 256,
    ) -> None:
        self.origin = os.environ.get("HERMES_G2_PLUGIN_ORIGIN", "http://127.0.0.1:8765")
        self.secret = os.environ.get("HERMES_G2_PLUGIN_SECRET", "")
        self._sender = sender
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue(
            maxsize=max(1, queue_size)
        )
        self._dropped = 0
        self._thread: threading.Thread | None = None
        if self.secret:
            self._thread = threading.Thread(
                target=self._deliver,
                name="hermes-g2-observer",
                daemon=True,
            )
            self._thread.start()

    @property
    def pending_count(self) -> int:
        return self._queue.qsize()

    @property
    def dropped_count(self) -> int:
        return self._dropped

    def close(self) -> None:
        if not self._thread:
            return
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                return
        self._thread.join(timeout=1)

    def _deliver(self) -> None:
        client = None if self._sender else httpx.Client(timeout=0.75)
        try:
            while True:
                envelope = self._queue.get()
                try:
                    if envelope is None:
                        return
                    if self._sender:
                        self._sender(envelope)
                    elif client:
                        client.post(
                            f"{self.origin}/internal/plugin/events",
                            headers={"X-Plugin-Secret": self.secret},
                            json=envelope,
                        )
                except Exception:  # noqa: BLE001, S110 -- observation stays fail-open
                    pass
                finally:
                    self._queue.task_done()
        finally:
            if client:
                client.close()

    def _send(self, kind: str, kwargs: dict[str, Any]) -> None:
        if not self.secret:
            return
        session_id = str(
            kwargs.get("session_id") or kwargs.get("sessionId") or kwargs.get("session_key") or ""
        ) or None
        run_id = self._run_id(kwargs)
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
        envelope = {
            "kind": self._event_kind(kind),
            "source": "plugin",
            "sessionId": session_id,
            "runId": run_id,
            "payload": safe,
        }
        try:
            self._queue.put_nowait(envelope)
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
                self._dropped += 1
                self._queue.put_nowait(envelope)
            except (queue.Empty, queue.Full):
                self._dropped += 1

    @staticmethod
    def _run_id(kwargs: dict[str, Any]) -> str | None:
        return str(kwargs.get("run_id") or kwargs.get("runId") or "") or None

    @staticmethod
    def _event_kind(hook: str) -> str:
        return {
            "on_session_start": "session.updated", "on_session_end": "session.updated",
            "on_session_finalize": "session.updated", "on_session_reset": "session.updated",
            "pre_tool_call": "tool.started", "post_tool_call": "tool.completed",
            "pre_llm_call": "run.progress", "post_llm_call": "run.progress",
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
