import os
import threading
from dataclasses import dataclass
from collections.abc import Callable
from typing import Any

import httpx


@dataclass(frozen=True)
class _QueuedEvent:
    priority: int
    sequence: int
    envelope: dict[str, Any]


class _EventBuffer:
    """A bounded, non-blocking buffer that protects terminal events first.

    Hook callbacks run inside Hermes' execution path, so enqueueing may take a
    short mutex but can never wait for the network worker or an available slot.
    Lower-priority progress is discarded before approvals, failures, and
    lifecycle terminal events. A closed buffer drains what it already has and
    then tells the worker to exit.
    """

    def __init__(self, maxsize: int) -> None:
        self._maxsize = max(1, maxsize)
        self._condition = threading.Condition()
        self._events: list[_QueuedEvent] = []
        self._sequence = 0
        self._dropped = 0
        self._closed = False

    @property
    def pending_count(self) -> int:
        with self._condition:
            return len(self._events)

    @property
    def dropped_count(self) -> int:
        with self._condition:
            return self._dropped

    def put_nowait(self, envelope: dict[str, Any], *, priority: int) -> bool:
        """Enqueue without waiting for capacity or the delivery worker."""

        with self._condition:
            if self._closed:
                self._dropped += 1
                return False

            item = _QueuedEvent(priority, self._sequence, envelope)
            self._sequence += 1
            if len(self._events) >= self._maxsize:
                victim_index = min(
                    range(len(self._events)),
                    key=lambda index: (
                        self._events[index].priority,
                        self._events[index].sequence,
                    ),
                )
                victim = self._events[victim_index]
                # Keep already-buffered work when it has equal importance. A
                # newer critical event can still displace older progress.
                if priority <= victim.priority:
                    self._dropped += 1
                    return False
                self._events.pop(victim_index)
                self._dropped += 1

            self._events.append(item)
            self._condition.notify()
            return True

    def get(self) -> dict[str, Any] | None:
        with self._condition:
            while not self._events and not self._closed:
                self._condition.wait()
            if not self._events:
                return None
            next_index = max(
                range(len(self._events)),
                key=lambda index: (
                    self._events[index].priority,
                    -self._events[index].sequence,
                ),
            )
            return self._events.pop(next_index).envelope

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()


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
        self._queue = _EventBuffer(queue_size)
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
        return self._queue.pending_count

    @property
    def dropped_count(self) -> int:
        return self._queue.dropped_count

    def close(self) -> None:
        if not self._thread:
            return
        self._queue.close()
        self._thread.join(timeout=1)

    def _deliver(self) -> None:
        client = None if self._sender else httpx.Client(timeout=0.75)
        try:
            while True:
                envelope = self._queue.get()
                if envelope is None:
                    return
                try:
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
        self._queue.put_nowait(
            envelope,
            priority=self._event_priority(kind, event_kind=envelope["kind"], kwargs=kwargs),
        )

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
        }.get(hook, "unknown")

    @staticmethod
    def _event_priority(
        hook: str,
        *,
        event_kind: str,
        kwargs: dict[str, Any],
    ) -> int:
        """Rank events for bounded delivery; higher values are retained first."""

        status = str(kwargs.get("status") or "").strip().lower()
        if (
            kwargs.get("error")
            or kwargs.get("exception")
            or status
            in {
                "failed",
                "failure",
                "error",
                "errored",
                "exception",
                "crashed",
                "cancelled",
                "canceled",
                "interrupted",
                "aborted",
                "timeout",
            }
        ):
            return 100
        if event_kind == "attention.created" or hook == "pre_approval_request":
            return 100
        if event_kind == "attention.resolved" or hook == "post_approval_response":
            return 90
        if hook in {"on_session_end", "on_session_finalize", "on_session_reset"}:
            return 80
        if hook == "subagent_stop" or status in {"completed", "complete", "done"}:
            return 60
        if event_kind == "unknown":
            return 20
        return 10

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
