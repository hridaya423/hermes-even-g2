import asyncio
import base64
import io
import json
import math
from datetime import timedelta
from pathlib import Path
from typing import Any

from .hermes import HermesClient
from .models import ActionKind, AgentAction, EventInput, utc_now
from .security import redact
from .store import Store
from .summary import summarize

APPROVAL_MAP = {
    ActionKind.APPROVE_ONCE: "once",
    ActionKind.APPROVE_SESSION: "session",
    ActionKind.APPROVE_ALWAYS: "always",
    ActionKind.DENY: "deny",
}


def _bounded_text(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = redact(str(value))
    return text if len(text) <= limit else text[: limit - 1] + "…"


_KEY_ALIASES = {
    "toolname": "toolName",
    "currenttool": "currentTool",
    "changedfiles": "changedFiles",
    "changedfilecount": "changedFileCount",
    "durationms": "durationMs",
    "elapsedms": "elapsedMs",
    "exitcode": "exitCode",
    "finishreason": "finishReason",
    "requestid": "requestId",
    "runid": "runId",
    "sessionid": "sessionId",
    "queueid": "queueId",
    "queueposition": "queuePosition",
    "initiatedbyg2": "initiatedByG2",
    "expiresat": "expiresAt",
    "updatedat": "updatedAt",
    "structuredsummary": "structuredSummary",
    "suggestednextaction": "suggestedNextAction",
    "prompttokens": "promptTokens",
    "completiontokens": "completionTokens",
    "totaltokens": "totalTokens",
    "inputtokens": "inputTokens",
    "outputtokens": "outputTokens",
    "cachedtokens": "cachedTokens",
}


def _canonical_key(value: Any) -> str:
    compact = str(value).strip().replace("-", "").replace("_", "").lower()
    return _KEY_ALIASES.get(compact, str(value))


def _safe_text(value: Any, limit: int) -> str | None:
    # Stringifying arbitrary dicts/lists is both surprising to clients and an easy
    # way to smuggle an unbounded nested provider payload into SQLite.
    if not isinstance(value, (str, int, float, bool)):
        return None
    return _bounded_text(value, limit)


def _safe_number(value: Any, *, minimum: int, maximum: int) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return max(minimum, min(maximum, value))


def _project_choices(value: Any) -> list[Any] | None:
    if not isinstance(value, list):
        return None
    choices: list[Any] = []
    for item in value[:8]:
        if isinstance(item, (str, int, float, bool)):
            text = _safe_text(item, 160)
            if text is not None:
                choices.append(text)
            continue
        if not isinstance(item, dict):
            continue
        choice: dict[str, str] = {}
        for raw_key, raw_value in item.items():
            key = _canonical_key(raw_key)
            if key not in {"id", "label", "description", "value"}:
                continue
            text = _safe_text(raw_value, 240 if key == "description" else 120)
            if text is not None:
                choice[key] = text
        if choice:
            choices.append(choice)
    return choices


def _project_string_list(value: Any, limit: int, item_limit: int) -> list[str] | None:
    if not isinstance(value, list):
        return None
    projected: list[str] = []
    for item in value[:limit]:
        text = _safe_text(item, item_limit)
        if text is not None:
            projected.append(text)
    return projected


def _project_usage(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    allowed = {
        "promptTokens", "completionTokens", "totalTokens", "inputTokens",
        "outputTokens", "cachedTokens", "tokens",
    }
    usage: dict[str, int] = {}
    for raw_key, raw_value in value.items():
        key = _canonical_key(raw_key)
        if key not in allowed:
            continue
        number = _safe_number(raw_value, minimum=0, maximum=10_000_000)
        if number is not None:
            usage[key] = int(number)
    return usage or None


def _project_tests(value: Any) -> list[Any] | None:
    if not isinstance(value, list):
        return None
    projected: list[Any] = []
    allowed = {"name", "status", "durationMs", "summary", "error"}
    for item in value[:8]:
        if isinstance(item, (str, int, float, bool)):
            text = _safe_text(item, 240)
            if text is not None:
                projected.append(text)
            continue
        if not isinstance(item, dict):
            continue
        test: dict[str, Any] = {}
        for raw_key, raw_value in item.items():
            key = _canonical_key(raw_key)
            if key not in allowed:
                continue
            if key == "durationMs":
                number = _safe_number(raw_value, minimum=0, maximum=86_400_000)
                if number is not None:
                    test[key] = int(number)
            else:
                text = _safe_text(raw_value, 500)
                if text is not None:
                    test[key] = text
        if test:
            projected.append(test)
    return projected


def _project_summary(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    allowed = {
        "headline", "outcome", "keyChanges", "validation", "blocker",
        "suggestedNextAction",
    }
    summary: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = _canonical_key(raw_key)
        if key not in allowed:
            continue
        text = _safe_text(raw_value, 1200)
        if text is not None:
            summary[key] = text
    return summary or None


_COMMON_FIELDS = {
    "phase", "status", "state", "message", "text", "summary", "reason",
    "error", "details", "tool", "toolName", "currentTool", "command", "cwd",
    "path", "destination", "rule", "model", "provider", "runId", "sessionId",
    "requestId", "queueId", "queuePosition", "initiatedByG2", "retryable",
    "durationMs", "elapsedMs", "exitCode", "changedFileCount", "changedFiles",
    "tests", "usage", "choices", "expiresAt", "destructive", "sensitive",
}
_KIND_FIELDS = {
    "message.completed": _COMMON_FIELDS | {
        "role", "content", "finishReason", "structuredSummary", "headline",
    },
    "run.progress": _COMMON_FIELDS,
    "run.started": _COMMON_FIELDS,
    "run.completed": _COMMON_FIELDS,
    "run.failed": _COMMON_FIELDS,
    "run.cancelled": _COMMON_FIELDS,
    "tool.started": _COMMON_FIELDS,
    "tool.completed": _COMMON_FIELDS,
    "tool.failed": _COMMON_FIELDS,
    "approval.required": _COMMON_FIELDS,
    "approval.resolved": _COMMON_FIELDS | {"choice"},
    "attention.created": _COMMON_FIELDS | {"kind"},
    "attention.resolved": _COMMON_FIELDS | {"kind", "choice"},
    "session.updated": {
        "id", "title", "state", "updatedAt", "source", "provider", "model",
        "workspace", "project", "queuePosition", "latestAnswer",
    },
}
_TEXT_FIELDS = {
    "phase", "status", "state", "message", "text", "summary", "reason", "error",
    "details", "tool", "toolName", "currentTool", "command", "cwd", "path",
    "destination", "rule", "model", "provider", "runId", "sessionId", "requestId",
    "queueId", "expiresAt", "role", "content", "finishReason", "choice", "kind",
    "id", "title", "updatedAt", "source", "workspace", "project", "latestAnswer",
    "headline", "outcome", "validation", "blocker", "suggestedNextAction",
}
_BOOL_FIELDS = {"initiatedByG2", "retryable", "destructive", "sensitive"}
_NUMBER_FIELDS = {
    "durationMs": (0, 86_400_000), "elapsedMs": (0, 86_400_000),
    "exitCode": (-255, 255), "changedFileCount": (0, 100_000), "queuePosition": (0, 100_000),
}
_REDACT_FIELDS = {"stdout", "stderr", "output"}
_SENSITIVE_FIELDS = {
    "apikey", "authorization", "credential", "environment", "env", "headers",
    "password", "secret", "token",
}


def sanitize_event_payload(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Project provider events into a small, allowlisted and bounded safe shape.

    Provider hooks routinely include raw request arguments, environment maps and
    nested tool transcripts.  Redaction alone is not sufficient because unknown
    future fields can still leak or create unbounded rows, so unknown keys and
    nested objects are dropped unless they have an explicit bounded projection.
    """
    if not isinstance(payload, dict):
        return {"value": _safe_text(payload, 2000)}
    allowed = _KIND_FIELDS.get(kind, _COMMON_FIELDS)
    sanitized: dict[str, Any] = {}
    for raw_key, item in list(payload.items())[:64]:
        key = _canonical_key(raw_key)
        compact_key = str(raw_key).strip().replace("-", "").replace("_", "").lower()
        if compact_key in _SENSITIVE_FIELDS or any(
            marker in compact_key for marker in ("password", "secret", "credential", "token")
        ):
            # Retain a small, explicit redaction marker for top-level sensitive
            # fields so clients can explain why context is unavailable.  We never
            # recurse into the value, which prevents nested raw payload retention.
            sanitized[str(raw_key)] = "<redacted>"
            continue
        if key in _REDACT_FIELDS:
            # Preserve the fact that output existed without persisting arbitrary
            # command output, which can contain secrets and huge transcripts.
            sanitized[key] = "<redacted>"
        elif key not in allowed:
            continue
        elif key in _TEXT_FIELDS:
            text = _safe_text(item, 12_000 if kind == "message.completed" and key in {"content", "message", "text"} else 2_000)
            if text is not None:
                sanitized[key] = text
        elif key in _BOOL_FIELDS and isinstance(item, bool):
            sanitized[key] = item
        elif key in _NUMBER_FIELDS:
            minimum, maximum = _NUMBER_FIELDS[key]
            number = _safe_number(item, minimum=minimum, maximum=maximum)
            if number is not None:
                sanitized[key] = int(number) if isinstance(number, float) and number.is_integer() else number
        elif key == "choices":
            projected = _project_choices(item)
            if projected is not None:
                sanitized[key] = projected
        elif key == "changedFiles":
            projected = _project_string_list(item, 50, 300)
            if projected is not None:
                sanitized[key] = projected
        elif key == "tests":
            projected = _project_tests(item)
            if projected is not None:
                sanitized[key] = projected
        elif key == "usage":
            projected = _project_usage(item)
            if projected is not None:
                sanitized[key] = projected
        elif key == "structuredSummary":
            projected = _project_summary(item)
            if projected is not None:
                sanitized[key] = projected
    return sanitized


def _encode_image_data_url(path: Path, media_type: str, size: int, max_bytes: int) -> str:
    if size > max_bytes:
        raise ValueError("image attachment is too large to send inline")
    encoded = io.StringIO()
    carry = b""
    with path.open("rb") as source:
        while chunk := source.read(60 * 1024):
            data = carry + chunk
            usable = len(data) - (len(data) % 3)
            if usable:
                encoded.write(base64.b64encode(data[:usable]).decode("ascii"))
            carry = data[usable:]
    if carry:
        encoded.write(base64.b64encode(carry).decode("ascii"))
    return f"data:{media_type};base64,{encoded.getvalue()}"


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
        inline_image_max_bytes: int = 8 * 1024 * 1024,
    ):
        self.store = store
        self.hermes = hermes
        self.action_max_age = action_max_age_seconds
        self.summary_helper = summary_helper
        self.whisper_binary = whisper_binary
        self.whisper_model = whisper_model
        self.tailscale_cli = tailscale_cli
        self.inline_image_max_bytes = inline_image_max_bytes
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
                await self.store.append_event(EventInput(kind="session.updated", source="hermes", sessionId=session_id, payload=sanitize_event_payload("session.updated", session)))
                changed += 1
            if session_id and session.get("state") == "idle" and not await self.store.session_turn_active(session_id):
                queued = await self.store.claim_next_prompt(session_id)
                if queued:
                    self._start_prompt(
                        session_id,
                        queued.get("message", queued.get("text", "")),
                        queued["deviceId"],
                        queued.get("options"),
                        queued.get("attachmentIds", []),
                        queue_id=queued.get("queueId"),
                        admission_id=queued.get("claimToken"),
                    )
        return changed

    async def sessions(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        sessions = await self.hermes.list_sessions(limit, offset)
        overlays = await self.store.session_overlays([session["id"] for session in sessions])
        return [{**session, **overlays.get(session["id"], {})} for session in sessions]

    def require_core(self) -> None:
        if not self.runtime.get("coreReady"):
            raise ValueError("Hermes native session history and streaming are not ready")

    async def ensure_core(self) -> None:
        """Refresh capabilities before rejecting an action after a Hermes restart."""
        if not self.runtime.get("coreReady"):
            await self.probe()
        self.require_core()

    async def execute(self, action: AgentAction, device: dict[str, Any]) -> dict[str, Any]:
        if utc_now() - action.created_at > timedelta(seconds=self.action_max_age):
            raise ValueError("action is stale")
        if action.device_id != device["id"]:
            raise ValueError("action deviceId does not match authenticated device")
        if await self.store.device_exists(device["id"]) and not await self.store.is_device_active(device["id"]):
            raise ValueError("device is revoked or expired")
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
        await self.ensure_core()
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
            await self.store.append_event(EventInput(
                kind="session.updated",
                source="bridge",
                sessionId=action.session_id,
                payload=sanitize_event_payload("session.updated", renamed),
            ))
            return renamed
        if kind == ActionKind.SET_SESSION_MODEL:
            if not self.capabilities.get("models"):
                raise ValueError("installed Hermes does not advertise model options")
            provider = str(action.payload.get("provider", "")).strip()
            model = str(action.payload.get("model", "")).strip()
            if not provider or not model:
                raise ValueError("provider and model are required")
            response = await self.hermes.set_session_model(action.session_id, provider, model)
            await self.store.append_event(EventInput(kind="session.updated", source="bridge", sessionId=action.session_id, payload={"provider": provider, "model": model, "modelLock": "accepted"}))
            return response
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
            if not await self.store.run_is_active(action.session_id, action.run_id):
                raise ValueError("active run does not match this session and run")
            return await self.hermes.stop_run(action.session_id, action.run_id)
        if kind in {ActionKind.PROMPT, ActionKind.QUEUE_PROMPT}:
            text = str(action.payload.get("text", "")).strip()
            raw_attachment_ids = action.payload.get("attachmentIds", [])
            if isinstance(raw_attachment_ids, str):
                attachment_ids = [value.strip() for value in raw_attachment_ids.split(",") if value.strip()]
            elif isinstance(raw_attachment_ids, list):
                attachment_ids = [str(value).strip() for value in raw_attachment_ids if str(value).strip()]
            else:
                raise ValueError("attachmentIds must be a list or comma-separated string")
            if len(attachment_ids) > 10:
                raise ValueError("a prompt may include at most 10 attachments")
            if not text and not attachment_ids:
                raise ValueError("prompt text is empty")
            admission = await self.store.admit_prompt(
                action.session_id,
                {
                    "text": text or "Please inspect the attached file.",
                    "attachmentIds": attachment_ids,
                    "deviceId": device["id"],
                    "options": action.payload.get("options"),
                    "createdAt": action.created_at.isoformat(),
                },
                force_queue=kind == ActionKind.QUEUE_PROMPT,
            )
            if admission["status"] == "queued":
                position = admission["position"]
                await self.store.append_event(EventInput(kind="session.updated", source="bridge", sessionId=action.session_id, payload={"state": "queued", "queuePosition": position}))
                return {"status": "queued", "sessionId": action.session_id, "position": position}
            self._start_prompt(
                action.session_id,
                text or "Please inspect the attached file.",
                device["id"],
                action.payload.get("options"),
                attachment_ids,
                queue_id=admission["queueId"],
                admission_id=admission["admissionId"],
            )
            return {"status": "started", "sessionId": action.session_id}
        if kind in {ActionKind.RUN_JOB, ActionKind.PAUSE_JOB, ActionKind.RESUME_JOB}:
            if not self.capabilities.get("jobs"):
                raise ValueError("installed Hermes does not advertise jobs")
            verb = {ActionKind.RUN_JOB: "run", ActionKind.PAUSE_JOB: "pause", ActionKind.RESUME_JOB: "resume"}[kind]
            return await self.hermes.job_action(str(action.payload["jobId"]), verb)
        raise ValueError(f"unsupported action {kind}")

    async def prepare_prompt(
        self,
        session_id: str,
        device_id: str,
        text: str,
        attachment_ids: list[str],
    ) -> str | list[dict[str, Any]]:
        attachments = await self.store.claim_attachments(device_id, session_id, attachment_ids)
        if not attachments:
            return text
        document_lines = [
            f"- {item['name']}: {item['path']}"
            for item in attachments
            if not item["mediaType"].startswith("image/")
        ]
        prompt_text = text
        if document_lines:
            prompt_text += "\n\nAttached files staged on this Mac for this session:\n" + "\n".join(document_lines)
        image_parts = []
        for item in attachments:
            if not item["mediaType"].startswith("image/"):
                continue
            image_parts.append({
                "type": "image_url",
                "image_url": {
                    "url": _encode_image_data_url(Path(item["path"]), item["mediaType"], int(item["size"]), self.inline_image_max_bytes),
                    "detail": "auto",
                },
            })
        if not image_parts:
            return prompt_text
        return [{"type": "text", "text": prompt_text}, *image_parts]

    def _start_prompt(
        self,
        session_id: str,
        message: Any,
        device_id: str,
        options: dict | None,
        attachment_ids: list[str],
        *,
        prepared: bool = False,
        queue_id: str | None = None,
        admission_id: str | None = None,
    ) -> None:
        task = asyncio.create_task(
            self._run_prompt(
                session_id,
                message,
                device_id,
                options,
                attachment_ids,
                prepared=prepared,
                queue_id=queue_id,
                admission_id=admission_id,
            )
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run_prompt(
        self,
        session_id: str,
        message: Any,
        device_id: str,
        options: dict | None,
        attachment_ids: list[str],
        *,
        prepared: bool = False,
        queue_id: str | None = None,
        admission_id: str | None = None,
    ) -> None:
        run_id = None
        provider_failure = None
        try:
            if not await self.store.is_device_active(device_id):
                raise ValueError("device was revoked before prompt execution")
            if not prepared:
                message = await self.prepare_prompt(
                    session_id,
                    device_id,
                    str(message),
                    attachment_ids,
                )
            async for raw in self.hermes.stream_prompt(session_id, message, options):
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
                    payload = {**sanitize_event_payload(kind, raw), "initiatedByG2": True}
                    if run_id and kind in {"run.started", "run.completed", "run.failed", "run.cancelled"}:
                        status = kind.removeprefix("run.")
                        if kind == "run.started" and admission_id:
                            await self.store.bind_admission(admission_id, queue_id, session_id, run_id, device_id)
                        else:
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
                            "command": _bounded_text(raw.get("command"), 1000),
                            "destination": _bounded_text(raw.get("destination"), 1000),
                            "rule": _bounded_text(raw.get("rule"), 1000),
                            "destructive": bool(raw.get("destructive")),
                            "sensitive": bool(raw.get("sensitive") or raw.get("secret_bearing")),
                            "choices": [str(choice) for choice in raw.get("choices", ["once", "session", "always", "deny"])][:8],
                            "expiresAt": raw.get("expires_at"),
                        }
                    await self.store.append_event(EventInput(kind=kind if kind in EVENT_KINDS else "run.progress", source="hermes", sessionId=session_id, runId=run_id, payload=sanitize_event_payload(kind, payload)))
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
            await self.store.append_event(EventInput(kind="run.failed", source="bridge", sessionId=session_id, runId=run_id, payload=sanitize_event_payload("run.failed", {"error": str(error)[:500]})))
            await self.store.audit(device_id, "prompt", session_id, run_id, "failed", {"errorType": type(error).__name__})
        finally:
            await self.store.delete_consumed_attachments(session_id, attachment_ids)
            if queue_id:
                await self.store.complete_prompt(queue_id, session_id)
            if admission_id:
                await self.store.release_admission(session_id, admission_id)


EVENT_KINDS = {"runtime.updated", "session.created", "session.updated", "message.completed", "run.started", "run.progress", "run.completed", "run.failed", "run.cancelled", "tool.started", "tool.completed", "tool.failed", "approval.required", "approval.resolved", "subagent.started", "subagent.completed", "job.updated", "attention.created", "attention.resolved"}
