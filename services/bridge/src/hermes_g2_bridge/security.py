import hmac
import re
from typing import Any

from fastapi import Header, HTTPException, Request, WebSocket

from .store import Store

SECRET_PATTERN = re.compile(r"(?i)(api[_-]?key|authorization|token|password|secret)(\s*[:=]\s*)([^\s,;]+)")
PATH_PATTERN = re.compile(r"/(?:Users|home)/[^/\s]+/(?:[^\s]+)")


def redact(value: Any) -> Any:
    if isinstance(value, str):
        return PATH_PATTERN.sub("<private-path>", SECRET_PATTERN.sub(r"\1\2<redacted>", value))
    if isinstance(value, dict):
        return {key: ("<redacted>" if any(term in key.lower() for term in ("token", "secret", "password", "key")) else redact(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def require_scope(scope: str):
    async def dependency(request: Request, authorization: str = Header(default=""), x_device_id: str = Header(default="")):
        if not authorization.startswith("Bearer ") or not x_device_id:
            raise HTTPException(401, "device credential required")
        device = await request.app.state.store.authenticate(x_device_id, authorization[7:])
        if not device:
            raise HTTPException(401, "invalid or revoked device credential")
        if scope not in device["scopes"]:
            raise HTTPException(403, f"device lacks {scope}")
        return device
    return dependency


async def authenticate_websocket(websocket: WebSocket, store: Store) -> dict[str, Any] | None:
    device_id = websocket.headers.get("x-device-id", "")
    token = websocket.headers.get("authorization", "")
    credential = token[7:] if token.startswith("Bearer ") else ""
    return await store.authenticate(device_id, credential) if device_id and credential else None


def verify_plugin(secret: str | None, supplied: str) -> bool:
    return bool(secret and supplied and hmac.compare_digest(secret, supplied))
