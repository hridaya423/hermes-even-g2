import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx


class HermesError(RuntimeError):
    pass


class HermesClient:
    def __init__(self, origin: str, api_key: str):
        self.origin = origin.rstrip("/")
        self.client = httpx.AsyncClient(base_url=self.origin, headers={"Authorization": f"Bearer {api_key}"}, timeout=30)
        self._session_locks: dict[str, asyncio.Lock] = {}

    async def close(self) -> None:
        await self.client.aclose()

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        response = await self.client.request(method, path, **kwargs)
        if response.is_error:
            raise HermesError(f"Hermes {method} {path} returned {response.status_code}: {response.text[:300]}")
        return response.json()

    async def probe(self) -> dict[str, Any]:
        health, detailed, raw = await asyncio.gather(
            self._request("GET", "/health"),
            self._request("GET", "/health/detailed"),
            self._request("GET", "/v1/capabilities"),
        )
        features = raw.get("features", raw.get("capabilities", raw)) if isinstance(raw, dict) else {}
        flattened = set(features)
        def enabled(*names: str) -> bool:
            return any(name in flattened and (not isinstance(features, dict) or bool(features.get(name))) for name in names)
        return {
            "healthy": bool(health), "detailed": detailed,
            "nativeSessions": enabled("native_sessions", "sessions", "session_resources", "session_chat"),
            "sessionHistory": enabled("session_history", "sessions", "session_resources"),
            "sessionStreaming": enabled("session_streaming", "streaming", "session_chat_streaming"),
            "sessionRunControl": enabled("session_run_control"),
            "sessionApprovalResponse": enabled("session_approval_response"),
            "jobs": enabled("jobs", "jobs_admin"), "models": enabled("models", "model_options"), "skills": enabled("skills", "skills_api"),
            "subagents": enabled("subagents"), "attachments": enabled("attachments"), "raw": raw,
        }

    async def list_sessions(self, limit: int = 50, offset: int = 0) -> Any:
        value = await self._request("GET", "/api/sessions", params={"limit": limit, "offset": offset})
        rows = value.get("sessions", value.get("items", [])) if isinstance(value, dict) else value
        return [self._normalize_session(item) for item in rows]

    @staticmethod
    def _normalize_session(value: dict[str, Any]) -> dict[str, Any]:
        workspace = value.get("workspace") or value.get("working_directory") or value.get("cwd")
        raw_state = str(value.get("state") or value.get("status") or "idle").lower()
        state = raw_state if raw_state in {"idle", "busy", "queued", "failed", "unbound"} else "idle"
        if not workspace and bool(value.get("workspace_required")):
            state = "unbound"
        return {
            "id": str(value.get("id") or value.get("session_id") or ""),
            "title": str(value.get("title") or value.get("name") or "Untitled"),
            "source": str(value.get("source") or "unknown"),
            "model": value.get("model"), "provider": value.get("provider"),
            "parentSessionId": value.get("parent_session_id") or value.get("parentSessionId"),
            "workspace": workspace,
            "executionReady": state != "unbound",
            "state": state,
            "updatedAt": str(value.get("updated_at") or value.get("updatedAt") or ""),
            "pinned": bool(value.get("pinned", False)),
            "latestAnswer": value.get("latest_answer") or value.get("latestAnswer"),
        }

    async def messages(self, session_id: str, limit: int = 100, offset: int = 0) -> Any:
        return await self._request("GET", f"/api/sessions/{session_id}/messages", params={"limit": limit, "offset": offset})

    async def create_session(self, payload: dict[str, Any]) -> Any:
        return await self._request("POST", "/api/sessions", json={**payload, "source": "even_g2"})

    async def fork_session(self, session_id: str, payload: dict[str, Any]) -> Any:
        return await self._request("POST", f"/api/sessions/{session_id}/fork", json=payload)

    async def stream_prompt(self, session_id: str, text: str, options: dict[str, Any] | None = None) -> AsyncIterator[dict[str, Any]]:
        lock = self._session_locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            async with self.client.stream("POST", f"/api/sessions/{session_id}/chat/stream", json={"message": text, **(options or {})}, timeout=None) as response:
                if response.is_error:
                    body = (await response.aread()).decode(errors="replace")
                    raise HermesError(f"session stream failed with {response.status_code}: {body[:300]}")
                data_lines: list[str] = []
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                    elif not line and data_lines:
                        raw = "\n".join(data_lines)
                        data_lines.clear()
                        yield json.loads(raw)

    async def stop_run(self, session_id: str, run_id: str) -> Any:
        return await self._request("POST", f"/v1/runs/{run_id}/stop", json={"session_id": session_id})

    async def approve(self, session_id: str, run_id: str, choice: str) -> Any:
        return await self._request("POST", f"/v1/runs/{run_id}/approval", json={"session_id": session_id, "choice": choice})

    async def job_action(self, job_id: str, action: str) -> Any:
        return await self._request("POST", f"/api/jobs/{job_id}/{action}")

    async def jobs(self) -> Any:
        return await self._request("GET", "/api/jobs")

    async def models(self) -> Any:
        return await self._request("GET", "/v1/models")

    async def skills(self) -> dict[str, Any]:
        skills, toolsets = await asyncio.gather(self._request("GET", "/v1/skills"), self._request("GET", "/v1/toolsets"))
        return {"skills": skills, "toolsets": toolsets}
