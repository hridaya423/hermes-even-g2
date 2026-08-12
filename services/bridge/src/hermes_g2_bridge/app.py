import asyncio
import hashlib
import json
import logging
import os
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import (
    Depends,
    FastAPI,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse, StreamingResponse

from .config import Settings
from .hermes import HermesClient, HermesError
from .models import AgentAction, EventInput, PairingExchange
from .security import authenticate_websocket, require_scope, verify_plugin
from .service import ControlService, sanitize_event_payload
from .store import Store
from .stt import SpeechError, transcribe

logger = logging.getLogger(__name__)


class ExternalBasePathMiddleware:
    def __init__(self, app, base_path: str):
        self.app = app
        self.base_path = "/" + base_path.strip("/") if base_path.strip("/") else ""

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if self.base_path and scope["type"] in {"http", "websocket"} and (
            path == self.base_path or path.startswith(f"{self.base_path}/")
        ):
            scope = dict(scope)
            scope["root_path"] = f"{scope.get('root_path', '')}{self.base_path}"
            scope["path"] = path[len(self.base_path):] or "/"
            scope["raw_path"] = scope["path"].encode()
        await self.app(scope, receive, send)


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings()
    store = Store(
        config.database_path,
        config.attachments_root,
        config.attachment_orphan_grace_seconds,
    )
    hermes = HermesClient(config.hermes_origin, config.hermes_api_key.get_secret_value())
    service = ControlService(
        store,
        hermes,
        config.action_max_age_seconds,
        config.summary_helper,
        config.whisper_binary,
        config.whisper_model,
        config.tailscale_cli,
        config.inline_image_max_bytes,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await store.migrate()
        await store.cleanup_attachments()
        recovered = await store.recover_inflight_prompts()
        if recovered:
            # Recovery only marks interrupted work and emits durable attention
            # events.  Nothing is automatically re-run after a bridge restart.
            logger.warning("Recovered %d interrupted Hermes prompt(s)", len(recovered))
        await service.probe()
        async def reconcile():
            while True:
                try:
                    await service.probe()
                    await service.reconcile_once()
                    await store.compact_events(config.event_retention_days, config.event_retention_floor)
                    await store.cleanup_attachments()
                except Exception as error:
                    logger.warning("Hermes reconciliation failed: %s", type(error).__name__)
                await asyncio.sleep(15)
        reconciliation = asyncio.create_task(reconcile())
        yield
        reconciliation.cancel()
        await hermes.close()

    app = FastAPI(title="Hermes G2 Bridge", version="0.1.0", lifespan=lifespan)
    app.add_middleware(ExternalBasePathMiddleware, base_path=config.external_base_path)
    app.state.store, app.state.settings, app.state.control = store, config, service

    @app.exception_handler(HermesError)
    async def hermes_dependency_error(_request: Request, error: HermesError):
        status = error.status_code if 400 <= error.status_code < 500 else 503
        return JSONResponse(
            status_code=status,
            content={
                "detail": str(error),
                "code": "hermes_unavailable" if status == 503 else "hermes_error",
            },
        )

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/health/detailed")
    async def detailed(device=Depends(require_scope("diagnostics:read"))):
        return await service.probe()

    @app.get("/v1/capabilities")
    async def capabilities(device=Depends(require_scope("sessions:read"))):
        return await service.probe()

    @app.post("/v1/pairings/exchange")
    async def exchange(value: PairingExchange):
        try:
            device_id, credential, scopes = await store.exchange_pairing(value.code, value.device_name, value.device_kind)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        return {"deviceId": device_id, "credential": credential, "scopes": scopes, "protocolVersion": "1.0"}

    @app.get("/v1/snapshot")
    async def snapshot(device=Depends(require_scope("sessions:read"))):
        probe, sessions = await asyncio.gather(service.probe(), service.sessions())
        async with store.connect() as db:
            active = [
                {
                    "runId": row["run_id"],
                    "sessionId": row["session_id"],
                    "deviceId": row["device_id"],
                    "initiatedByG2": bool(row["initiated_by_g2"]),
                    "status": row["status"],
                    "updatedAt": row["updated_at"],
                }
                for row in await db.execute_fetchall(
                    "SELECT * FROM run_correlation "
                    "WHERE status NOT IN ('completed','failed','cancelled')"
                )
            ]
            cursor_row = await (await db.execute("SELECT COALESCE(MAX(cursor),0) AS cursor FROM events")).fetchone()
            approval_rows = await db.execute_fetchall("SELECT run_id,payload_json FROM events WHERE kind='approval.required' ORDER BY cursor DESC LIMIT 100")
            resolved_rows = await db.execute_fetchall("SELECT run_id,payload_json FROM events WHERE kind='approval.resolved'")
        resolved = {
            (row["run_id"], json.loads(row["payload_json"]).get("requestId"))
            for row in resolved_rows
        }
        pending = []
        seen = set()
        for row in approval_rows:
            payload = json.loads(row["payload_json"])
            key = (row["run_id"], payload.get("requestId"))
            if key not in resolved and key not in seen:
                pending.append(payload)
                seen.add(key)
        return {"protocolVersion": "1.0", **probe, "sessions": sessions, "activeRuns": active, "pendingApprovals": pending, "cursor": cursor_row["cursor"]}

    @app.get("/v1/sessions")
    async def sessions(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), device=Depends(require_scope("sessions:read"))):
        return await service.sessions(limit, offset)

    @app.get("/v1/sessions/{session_id}/messages")
    async def messages(session_id: str, limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0), device=Depends(require_scope("sessions:read"))):
        return await hermes.messages(session_id, limit, offset)

    @app.get("/v1/jobs")
    async def jobs(device=Depends(require_scope("sessions:read"))):
        if not service.capabilities.get("jobs"):
            raise HTTPException(404, "installed Hermes does not advertise jobs")
        return await hermes.jobs()

    @app.get("/v1/models")
    async def models(device=Depends(require_scope("sessions:read"))):
        if not service.capabilities.get("models"):
            raise HTTPException(404, "installed Hermes does not advertise model options")
        return await hermes.models()

    @app.get("/v1/model-options")
    async def model_options(device=Depends(require_scope("sessions:read"))):
        if not service.capabilities.get("models"):
            raise HTTPException(404, "installed Hermes does not advertise model options")
        return await hermes.model_options()

    @app.get("/v1/skills")
    async def skills(device=Depends(require_scope("sessions:read"))):
        if not service.capabilities.get("skills"):
            raise HTTPException(404, "installed Hermes does not advertise skills")
        return await hermes.skills()

    @app.get("/v1/audit")
    async def audit(limit: int = Query(100, ge=1, le=500), device=Depends(require_scope("diagnostics:read"))):
        async with store.connect() as db:
            rows = await db.execute_fetchall("SELECT timestamp,device_id,action,session_fingerprint,run_fingerprint,outcome,detail_json FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
        return [{**dict(row), "detail": json.loads(row["detail_json"])} for row in rows]

    @app.get("/v1/devices")
    async def devices(device=Depends(require_scope("devices:manage"))):
        async with store.connect() as db:
            rows = await db.execute_fetchall("SELECT id,name,kind,scopes_json,created_at,expires_at,revoked_at,acknowledged_cursor FROM devices ORDER BY created_at DESC")
        return [{**dict(row), "scopes": json.loads(row["scopes_json"])} for row in rows]

    @app.post("/v1/devices/{device_id}/revoke")
    async def revoke(device_id: str, device=Depends(require_scope("sessions:read"))):
        if device_id != device["id"] and "devices:manage" not in device["scopes"]:
            raise HTTPException(403, "device may only revoke itself")
        if not await store.revoke_device(device_id):
            raise HTTPException(404, "device does not exist")
        await store.audit(device["id"], "revokeDevice", None, None, "completed", {"target": device_id[:8]})
        return {"status": "revoked"}

    @app.post("/v1/actions")
    async def actions(action: AgentAction, request: Request, device=Depends(require_scope("sessions:write"))):
        body = await request.body()
        try:
            fresh, cached_status, cached = await store.idempotency_begin(
                device["id"], action.idempotency_key, body
            )
            if not fresh:
                if cached is None:
                    raise HTTPException(409, "matching action is still in progress")
                if cached_status and cached_status >= 400:
                    raise HTTPException(cached_status, cached.get("detail", "action failed"))
                return cached
            response = await service.execute(action, device)
            await store.idempotency_finish(device["id"], action.idempotency_key, 200, response)
            await store.audit(device["id"], action.kind, action.session_id, action.run_id, "accepted")
            return response
        except HTTPException:
            raise
        except ValueError as error:
            await store.idempotency_finish(
                device["id"], action.idempotency_key, 409, {"detail": str(error)}
            )
            await store.audit(device["id"], action.kind, action.session_id, action.run_id, "rejected", {"reason": str(error)[:160]})
            raise HTTPException(409, str(error)) from error
        except HermesError as error:
            status = 409 if error.status_code in {400, 409} else 502
            await store.idempotency_finish(
                device["id"], action.idempotency_key, status, {"detail": str(error)}
            )
            await store.audit(
                device["id"], action.kind, action.session_id, action.run_id, "failed",
                {"reason": str(error)[:160]},
            )
            raise HTTPException(status, str(error)) from error

    @app.post("/v1/audio")
    async def audio(request: Request, session_id: str = Query(alias="sessionId"), device=Depends(require_scope("audio:write"))):
        try:
            result = await transcribe(await request.body(), config.whisper_binary, config.whisper_model)
        except SpeechError as error:
            raise HTTPException(422, str(error)) from error
        return {**result, "sessionId": session_id}

    @app.post("/v1/attachments", status_code=201)
    async def upload_attachment(
        session_id: str = Query(alias="sessionId", min_length=1, max_length=256),
        file: UploadFile = File(...),
        device=Depends(require_scope("attachments:write")),
    ):
        sessions = await service.sessions(limit=100)
        if not any(str(session.get("id")) == session_id for session in sessions):
            raise HTTPException(404, "target session does not exist")
        await store.cleanup_attachments()
        original_name = file.filename or "attachment"
        safe_name = re.sub(r"[\x00-\x1f\x7f]", "", os.path.basename(original_name)).strip()
        if not safe_name or safe_name in {".", ".."}:
            raise HTTPException(422, "attachment filename is invalid")
        attachment_id = str(uuid.uuid4())
        session_bucket = hashlib.sha256(session_id.encode()).hexdigest()[:24]
        suffix = Path(safe_name).suffix[:16]
        directory = config.attachments_root / session_bucket
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = directory / f"{attachment_id}{suffix}"
        media_type = file.content_type or "application/octet-stream"
        digest_state = hashlib.sha256()
        size = 0
        try:
            with path.open("xb") as output:
                path.chmod(0o600)
                while chunk := await file.read(config.attachment_chunk_bytes):
                    size += len(chunk)
                    if size > config.attachment_max_bytes:
                        raise HTTPException(413, "attachment exceeds the configured size limit")
                    digest_state.update(chunk)
                    output.write(chunk)
            if size == 0:
                raise HTTPException(422, "attachment is empty")
            digest = digest_state.hexdigest()
            try:
                await store.record_attachment(
                    attachment_id,
                    device["id"],
                    session_id,
                    safe_name,
                    media_type,
                    path,
                    digest,
                    size,
                    ttl_seconds=config.attachment_ttl_seconds,
                    device_quota_bytes=config.attachment_device_quota_bytes,
                    total_quota_bytes=config.attachment_total_quota_bytes,
                )
            except ValueError as error:
                raise HTTPException(413, str(error)) from error
        except Exception:
            path.unlink(missing_ok=True)
            raise
        finally:
            await file.close()
        await store.audit(
            device["id"],
            "uploadAttachment",
            session_id,
            None,
            "completed",
            {"attachment": attachment_id[:8], "size": size, "mediaType": media_type},
        )
        return {
            "attachmentId": attachment_id,
            "sessionId": session_id,
            "name": safe_name,
            "mediaType": media_type,
            "size": size,
            "sha256": digest,
        }

    @app.get("/v1/events/replay")
    async def event_replay(
        after: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=500),
        device=Depends(require_scope("sessions:read")),
    ):
        rows = await store.events_after(after, limit + 1)
        events = rows[:limit]
        oldest, latest = await store.event_bounds()
        requires_snapshot = after > 0 and oldest > 0 and after < oldest - 1
        return {
            "events": events,
            "nextCursor": events[-1]["cursor"] if events else after,
            "hasMore": len(rows) > limit,
            "oldestCursor": oldest,
            "latestCursor": latest,
            "requiresSnapshot": requires_snapshot,
        }

    @app.get("/v1/events")
    async def events(after: int = Query(0, ge=0), device=Depends(require_scope("sessions:read"))):
        async def generate():
            async for event in store.event_stream(after, device_id=device["id"]):
                yield f"data: {json.dumps(event, separators=(',', ':'))}\n\n"
        return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.websocket("/v1/channel")
    async def channel(websocket: WebSocket):
        await websocket.accept()
        device = await authenticate_websocket(websocket, store)
        if not device:
            try:
                auth = await asyncio.wait_for(websocket.receive_json(), timeout=5)
                if auth.get("type") == "authenticate":
                    device = await store.authenticate(str(auth.get("deviceId", "")), str(auth.get("credential", "")))
            except (TimeoutError, ValueError):
                device = None
        if not device or "sessions:read" not in device["scopes"]:
            await websocket.close(code=4401)
            return
        after = int(websocket.query_params.get("after", "0"))

        async def send_events():
            async for event in store.event_stream(after, device_id=device["id"]):
                await websocket.send_json(event)
                if event.get("type") == "auth.revoked":
                    await websocket.close(code=4403)
                    return

        async def receive_acks():
            try:
                while True:
                    message = await websocket.receive_json()
                    if message.get("type") == "ack":
                        await store.acknowledge(device["id"], int(message["cursor"]))
            except WebSocketDisconnect:
                return

        try:
            sender = asyncio.create_task(send_events())
            receiver = asyncio.create_task(receive_acks())
            done, pending = await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            await asyncio.gather(*done, return_exceptions=True)
        except (WebSocketDisconnect, asyncio.CancelledError):
            return

    @app.post("/internal/plugin/events")
    async def plugin_event(event: EventInput, x_plugin_secret: str = Header(default="")):
        expected = config.plugin_secret.get_secret_value() if config.plugin_secret else None
        if not verify_plugin(expected, x_plugin_secret):
            raise HTTPException(401, "invalid plugin credential")
        event.payload = sanitize_event_payload(event.kind, event.payload)
        return await store.append_event(event)

    return app
