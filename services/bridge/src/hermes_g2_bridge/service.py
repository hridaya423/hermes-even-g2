import asyncio
import json
from datetime import timedelta
from typing import Any

from .hermes import HermesClient
from .models import ActionKind, AgentAction, EventInput, utc_now
from .store import Store
from .summary import summarize

APPROVAL_MAP = {
    ActionKind.APPROVE_ONCE: "once",
    ActionKind.APPROVE_SESSION: "session",
    ActionKind.APPROVE_ALWAYS: "always",
    ActionKind.DENY: "deny",
}


class ControlService:
    def __init__(
        self,
        store: Store,
        hermes: HermesClient,
        action_max_age_seconds: int,
        summary_helper,
        whisper_binary,
        whisper_model,
        tailscale_cli,
    ):
        self.store = store
        self.hermes = hermes
        self.action_max_age = action_max_age_seconds
        self.summary_helper = summary_helper
        self.whisper_binary = whisper_binary
        self.whisper_model = whisper_model
        self.tailscale_cli = tailscale_cli
        self.capabilities: dict[str, Any] = {}
        self.runtime: dict[str, Any] = {"bridge": True, "hermes": False, "coreReady": False, "guiReady": False}
        self._tasks: set[asyncio.Task] = set()

    async def probe(self) -> dict[str, Any]:
        self.runtime.update(
            stt=self.whisper_binary.exists() and self.whisper_model.exists(),
            summary=self.summary_helper.exists(),
            tailscale=await self._tailscale_ready(),
        )
        try:
            self.capabilities = await self.hermes.probe()
            detailed = self.capabilities.get("detailed", {})
            self.runtime.update(hermes=True, coreReady=all(self.capabilities.get(name) for name in ("nativeSessions", "sessionHistory", "sessionStreaming")), guiReady=bool(detailed.get("gui_ready", detailed.get("guiReady", False))), reason=None)
        except Exception as error:
            self.runtime.update(hermes=False, coreReady=False, guiReady=False, reason=str(error)[:200])
        return {"runtime": self.runtime, "hermes": self.capabilities}

    async def _tailscale_ready(self) -> bool:
        if not self.tailscale_cli.exists():
            return False
        try:
            process = await asyncio.create_subprocess_exec(
                str(self.tailscale_cli),
                "status",
                "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=2)
            status = json.loads(stdout)
            return process.returncode == 0 and status.get("BackendState") == "Running"
        except (OSError, TimeoutError, json.JSONDecodeError):
            return False

    async def reconcile_once(self) -> int:
        changed = 0
        sessions = await self.hermes.list_sessions(limit=100)
        for session in sessions:
            session_id = str(session.get("id", session.get("session_id", "")))
            updated_at = str(session.get("updated_at", session.get("updatedAt", "")))
            if session_id and await self.store.observe_session(session_id, updated_at):
                await self.store.append_event(EventInput(kind="session.updated", source="hermes", sessionId=session_id, payload=session))
                changed += 1
            if session_id and session.get("state") == "idle" and not await self.store.session_turn_active(session_id):
                queued = await self.store.dequeue_prompt(session_id)
                if queued:
                    self._start_prompt(session_id, queued["text"], queued["deviceId"], queued.get("options"))
        return changed

    async def sessions(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        sessions = await self.hermes.list_sessions(limit, offset)
        overlays = await self.store.session_overlays([session["id"] for session in sessions])
        return [{**session, **overlays.get(session["id"], {})} for session in sessions]

    def require_core(self) -> None:
        if not self.runtime.get("coreReady"):
            raise ValueError("Hermes native session history and streaming are not ready")

    async def execute(self, action: AgentAction, device: dict[str, Any]) -> dict[str, Any]:
        if utc_now() - action.created_at > timedelta(seconds=self.action_max_age):
            raise ValueError("action is stale")
        if action.device_id != device["id"]:
            raise ValueError("action deviceId does not match authenticated device")
        kind = action.kind
        required_scope = (
            "approvals:write" if kind in APPROVAL_MAP else
            "runs:control" if kind == ActionKind.STOP_RUN else
            "jobs:write" if kind in {ActionKind.RUN_JOB, ActionKind.PAUSE_JOB, ActionKind.RESUME_JOB} else
            "sessions:write"
        )
        if required_scope not in device["scopes"]:
            raise ValueError(f"device lacks {required_scope}")
        if kind == ActionKind.ACKNOWLEDGE:
            await self.store.acknowledge(device["id"], int(action.payload["cursor"]))
            return {"status": "acknowledged"}
        if kind in {ActionKind.PIN_SESSION, ActionKind.UNPIN_SESSION}:
            async with self.store.connect() as db:
                await db.execute("INSERT INTO session_state(session_id,pinned,updated_at) VALUES(?,?,?) ON CONFLICT(session_id) DO UPDATE SET pinned=excluded.pinned,updated_at=excluded.updated_at", (action.session_id, int(kind == ActionKind.PIN_SESSION), utc_now().isoformat()))
                await db.commit()
            return {"status": "ok", "pinned": kind == ActionKind.PIN_SESSION}
        self.require_core()
        if kind == ActionKind.CREATE_SESSION:
            created = await self.hermes.create_session(action.payload)
            await self.store.set_session_source(created["id"], "even_g2")
            return {**created, "source": "even_g2"}
        if kind == ActionKind.FORK_SESSION:
            return await self.hermes.fork_session(action.session_id, action.payload)
        if kind == ActionKind.RENAME_SESSION:
            title = str(action.payload.get("title", "")).strip()
            if not title or len(title) > 120:
                raise ValueError("session title must contain 1 to 120 characters")
            renamed = await self.hermes.rename_session(action.session_id, title)
            await self.store.append_event(EventInput(kind="session.updated", source="bridge", sessionId=action.session_id, payload=renamed))
            return renamed
        if kind in APPROVAL_MAP:
            if not self.capabilities.get("sessionApprovalResponse"):
                raise ValueError("installed Hermes does not advertise native session approval responses")
            if not await self.store.approval_is_pending(
                action.session_id,
                action.run_id,
                str(action.payload.get("requestId")) if action.payload.get("requestId") else None,
            ):
                raise ValueError("approval is stale, resolved, or does not match this session and run")
            response = await self.hermes.approve(action.session_id, action.run_id, APPROVAL_MAP[kind])
            await self.store.append_event(EventInput(kind="approval.resolved", source="bridge", sessionId=action.session_id, runId=action.run_id, payload={"requestId": action.payload.get("requestId"), "choice": APPROVAL_MAP[kind]}))
            return response
        if kind == ActionKind.STOP_RUN:
            if not self.capabilities.get("sessionRunControl"):
                raise ValueError("installed Hermes does not advertise native session run control")
            return await self.hermes.stop_run(action.session_id, action.run_id)
        if kind in {ActionKind.PROMPT, ActionKind.QUEUE_PROMPT}:
            text = str(action.payload.get("text", "")).strip()
            if not text:
                raise ValueError("prompt text is empty")
            sessions = await self.sessions(limit=100)
            target = next((item for item in sessions if item["id"] == action.session_id), None)
            should_queue = kind == ActionKind.QUEUE_PROMPT or target is None or await self.store.session_is_busy(action.session_id)
            if should_queue:
                position = await self.store.enqueue_prompt(action.session_id, {"text": text, "deviceId": device["id"], "options": action.payload.get("options"), "createdAt": action.created_at.isoformat()})
                await self.store.append_event(EventInput(kind="session.updated", source="bridge", sessionId=action.session_id, payload={"state": "queued", "queuePosition": position}))
                return {"status": "queued", "sessionId": action.session_id, "position": position}
            self._start_prompt(action.session_id, text, device["id"], action.payload.get("options"))
            return {"status": "started", "sessionId": action.session_id}
        if kind in {ActionKind.RUN_JOB, ActionKind.PAUSE_JOB, ActionKind.RESUME_JOB}:
            if not self.capabilities.get("jobs"):
                raise ValueError("installed Hermes does not advertise jobs")
            verb = {ActionKind.RUN_JOB: "run", ActionKind.PAUSE_JOB: "pause", ActionKind.RESUME_JOB: "resume"}[kind]
            return await self.hermes.job_action(str(action.payload["jobId"]), verb)
        raise ValueError(f"unsupported action {kind}")

    def _start_prompt(self, session_id: str, text: str, device_id: str, options: dict | None) -> None:
        task = asyncio.create_task(self._run_prompt(session_id, text, device_id, options))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run_prompt(self, session_id: str, text: str, device_id: str, options: dict | None) -> None:
        run_id = None
        provider_failure = None
        try:
            async for raw in self.hermes.stream_prompt(session_id, text, options):
                kind = raw.get("type", raw.get("event", "run.progress"))
                run_id = raw.get("run_id", raw.get("runId", run_id))
                if kind == "assistant.completed":
                    content = str(raw.get("content") or "")
                    if content.startswith("API call failed after "):
                        provider_failure = content
                if kind == "run.completed" and provider_failure:
                    kind = "run.failed"
                    raw = {**raw, "error": provider_failure}
                kind = {"assistant.completed": "message.completed", "approval.request": "approval.required", "done": "run.progress", "error": "run.failed"}.get(kind, kind)
                durable = kind not in {"assistant.delta", "message.delta", "token"}
                if durable:
                    payload = {**raw, "initiatedByG2": True}
                    if run_id and kind in {"run.started", "run.completed", "run.failed", "run.cancelled"}:
                        status = kind.removeprefix("run.")
                        await self.store.update_run(run_id, session_id, device_id, status, True)
                    if kind == "message.completed":
                        content = str(raw.get("content") or raw.get("message") or raw.get("text") or "")
                        if content:
                            structured = await summarize(content, self.summary_helper, self.store)
                            payload = {
                                **raw,
                                "summary": structured["headline"],
                                "structuredSummary": structured,
                            }
                    if kind == "approval.required":
                        payload = {
                            "requestId": str(raw.get("request_id") or raw.get("approval_id") or run_id),
                            "sessionId": session_id,
                            "runId": run_id,
                            "tool": str(raw.get("tool") or raw.get("tool_name") or "tool"),
                            "command": raw.get("command"),
                            "destination": raw.get("destination"),
                            "rule": raw.get("rule"),
                            "destructive": bool(raw.get("destructive")),
                            "sensitive": bool(raw.get("sensitive") or raw.get("secret_bearing")),
                            "choices": raw.get("choices", ["once", "session", "always", "deny"]),
                            "expiresAt": raw.get("expires_at"),
                        }
                    await self.store.append_event(EventInput(kind=kind if kind in EVENT_KINDS else "run.progress", source="hermes", sessionId=session_id, runId=run_id, payload=payload))
            await self.store.audit(
                device_id,
                "prompt",
                session_id,
                run_id,
                "failed" if provider_failure else "completed",
                {"reason": provider_failure[:160]} if provider_failure else None,
            )
        except Exception as error:
            if run_id:
                await self.store.update_run(run_id, session_id, device_id, "failed", True)
            await self.store.append_event(EventInput(kind="run.failed", source="bridge", sessionId=session_id, runId=run_id, payload={"error": str(error)[:500]}))
            await self.store.audit(device_id, "prompt", session_id, run_id, "failed", {"errorType": type(error).__name__})


EVENT_KINDS = {"runtime.updated", "session.created", "session.updated", "message.completed", "run.started", "run.progress", "run.completed", "run.failed", "run.cancelled", "tool.started", "tool.completed", "tool.failed", "approval.required", "approval.resolved", "subagent.started", "subagent.completed", "job.updated", "attention.created", "attention.resolved"}
