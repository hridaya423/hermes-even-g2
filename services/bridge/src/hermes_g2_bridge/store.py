import asyncio
import hashlib
import json
import secrets
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

import aiosqlite
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from .models import EventInput, utc_now

MIGRATIONS = [
    """
    CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS devices(
      id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
      credential_hash TEXT NOT NULL, scopes_json TEXT NOT NULL,
      created_at TEXT NOT NULL, expires_at TEXT, revoked_at TEXT, acknowledged_cursor INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS pairing_codes(
      code_hash TEXT PRIMARY KEY, scopes_json TEXT NOT NULL, kind TEXT NOT NULL,
      expires_at TEXT NOT NULL, used_at TEXT
    );
    CREATE TABLE IF NOT EXISTS events(
      cursor INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
      protocol_version TEXT NOT NULL, kind TEXT NOT NULL, timestamp TEXT NOT NULL,
      source TEXT NOT NULL, session_id TEXT, run_id TEXT, payload_json TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS events_session_cursor ON events(session_id, cursor);
    CREATE TABLE IF NOT EXISTS idempotency(
      device_id TEXT NOT NULL, idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL,
      status_code INTEGER, response_json TEXT, created_at TEXT NOT NULL,
      PRIMARY KEY(device_id, idempotency_key)
    );
    CREATE TABLE IF NOT EXISTS session_state(
      session_id TEXT PRIMARY KEY, pinned INTEGER NOT NULL DEFAULT 0,
      active_run_id TEXT, queued_prompts_json TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS run_correlation(
      run_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, device_id TEXT,
      initiated_by_g2 INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS observed_sessions(
      session_id TEXT PRIMARY KEY, upstream_updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS audit_log(
      id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, device_id TEXT,
      action TEXT NOT NULL, session_fingerprint TEXT, run_fingerprint TEXT,
      outcome TEXT NOT NULL, detail_json TEXT NOT NULL
    );
    """
]


SCOPES = {
    "hub": ["sessions:read", "sessions:write", "audio:write", "runs:control", "approvals:write"],
    "android": ["sessions:read", "sessions:write", "audio:write", "runs:control", "approvals:write", "jobs:write", "attachments:write", "devices:manage", "diagnostics:read"],
    "simulator": ["sessions:read", "sessions:write", "audio:write", "runs:control", "approvals:write", "diagnostics:read"],
}


class Store:
    def __init__(self, path: Path):
        self.path = path
        self._ph = PasswordHasher()
        self._condition = asyncio.Condition()

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[aiosqlite.Connection]:
        db = await aiosqlite.connect(self.path)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
        finally:
            await db.close()

    async def migrate(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with self.connect() as db:
            for index, sql in enumerate(MIGRATIONS, start=1):
                await db.executescript(sql)
                await db.execute("INSERT OR IGNORE INTO schema_migrations(version) VALUES(?)", (index,))
            await db.commit()

    async def create_pairing(self, kind: str, ttl_seconds: int) -> str:
        code = f"{secrets.randbelow(1_000_000):06d}"
        digest = hashlib.sha256(code.encode()).hexdigest()
        expires = utc_now() + timedelta(seconds=ttl_seconds)
        async with self.connect() as db:
            await db.execute(
                "INSERT INTO pairing_codes VALUES(?,?,?,?,NULL)",
                (digest, json.dumps(SCOPES[kind]), kind, expires.isoformat()),
            )
            await db.commit()
        return code

    async def exchange_pairing(self, code: str, name: str, kind: str) -> tuple[str, str, list[str]]:
        digest = hashlib.sha256(code.encode()).hexdigest()
        now = utc_now()
        async with self.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            row = await (await db.execute("SELECT * FROM pairing_codes WHERE code_hash=?", (digest,))).fetchone()
            if not row or row["used_at"] or row["kind"] != kind or row["expires_at"] <= now.isoformat():
                await db.rollback()
                raise ValueError("pairing code is invalid, expired, used, or for another device kind")
            credential = secrets.token_urlsafe(32)
            device_id = str(uuid.uuid4())
            expires = (now + timedelta(days=7)).isoformat() if kind == "simulator" else None
            await db.execute(
                "INSERT INTO devices VALUES(?,?,?,?,?,?,?,NULL,0)",
                (device_id, name, kind, self._ph.hash(credential), row["scopes_json"], now.isoformat(), expires),
            )
            await db.execute("UPDATE pairing_codes SET used_at=? WHERE code_hash=?", (now.isoformat(), digest))
            await db.commit()
            return device_id, credential, json.loads(row["scopes_json"])

    async def authenticate(self, device_id: str, credential: str) -> dict[str, Any] | None:
        async with self.connect() as db:
            row = await (await db.execute("SELECT * FROM devices WHERE id=?", (device_id,))).fetchone()
        if not row or row["revoked_at"] or (row["expires_at"] and row["expires_at"] <= utc_now().isoformat()):
            return None
        try:
            self._ph.verify(row["credential_hash"], credential)
        except VerifyMismatchError:
            return None
        return {"id": row["id"], "kind": row["kind"], "scopes": json.loads(row["scopes_json"])}

    async def append_event(self, event: EventInput) -> dict[str, Any]:
        event_id = str(uuid.uuid4())
        timestamp = utc_now().isoformat()
        async with self.connect() as db:
            cursor = (await db.execute(
                "INSERT INTO events(event_id,protocol_version,kind,timestamp,source,session_id,run_id,payload_json) VALUES(?,?,?,?,?,?,?,?)",
                (event_id, "1.0", event.kind, timestamp, event.source, event.session_id, event.run_id, json.dumps(event.payload, separators=(",", ":"))),
            )).lastrowid
            await db.commit()
        value = {"protocolVersion": "1.0", "eventId": event_id, "cursor": cursor, "kind": event.kind, "timestamp": timestamp, "source": event.source, "sessionId": event.session_id, "runId": event.run_id, "payload": event.payload}
        async with self._condition:
            self._condition.notify_all()
        return value

    async def events_after(self, cursor: int, limit: int = 500) -> list[dict[str, Any]]:
        async with self.connect() as db:
            rows = await db.execute_fetchall("SELECT * FROM events WHERE cursor>? ORDER BY cursor LIMIT ?", (cursor, limit))
        return [{"protocolVersion": row["protocol_version"], "eventId": row["event_id"], "cursor": row["cursor"], "kind": row["kind"], "timestamp": row["timestamp"], "source": row["source"], "sessionId": row["session_id"], "runId": row["run_id"], "payload": json.loads(row["payload_json"])} for row in rows]

    async def event_stream(self, after: int) -> AsyncIterator[dict[str, Any]]:
        cursor = after
        while True:
            events = await self.events_after(cursor)
            if events:
                for event in events:
                    cursor = event["cursor"]
                    yield event
                continue
            async with self._condition:
                try:
                    await asyncio.wait_for(self._condition.wait(), timeout=20)
                except TimeoutError:
                    yield {"type": "keepalive", "cursor": cursor}

    async def idempotency_begin(self, device_id: str, key: str, body: bytes) -> tuple[bool, dict[str, Any] | None]:
        request_hash = hashlib.sha256(body).hexdigest()
        async with self.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            row = await (await db.execute("SELECT * FROM idempotency WHERE device_id=? AND idempotency_key=?", (device_id, key))).fetchone()
            if row:
                await db.rollback()
                if row["request_hash"] != request_hash:
                    raise ValueError("idempotency key was already used with another request body")
                return False, json.loads(row["response_json"]) if row["response_json"] else None
            await db.execute("INSERT INTO idempotency VALUES(?,?,?,?,?,?)", (device_id, key, request_hash, None, None, utc_now().isoformat()))
            await db.commit()
            return True, None

    async def idempotency_finish(self, device_id: str, key: str, status: int, response: dict[str, Any]) -> None:
        async with self.connect() as db:
            await db.execute("UPDATE idempotency SET status_code=?, response_json=? WHERE device_id=? AND idempotency_key=?", (status, json.dumps(response), device_id, key))
            await db.commit()

    async def acknowledge(self, device_id: str, cursor: int) -> None:
        async with self.connect() as db:
            await db.execute("UPDATE devices SET acknowledged_cursor=MAX(acknowledged_cursor,?) WHERE id=?", (cursor, device_id))
            await db.commit()

    async def audit(self, device_id: str | None, action: str, session_id: str | None, run_id: str | None, outcome: str, detail: dict[str, Any] | None = None) -> None:
        fingerprint = lambda value: hashlib.sha256(value.encode()).hexdigest()[:12] if value else None
        async with self.connect() as db:
            await db.execute("INSERT INTO audit_log(timestamp,device_id,action,session_fingerprint,run_fingerprint,outcome,detail_json) VALUES(?,?,?,?,?,?,?)", (utc_now().isoformat(), device_id, action, fingerprint(session_id), fingerprint(run_id), outcome, json.dumps(detail or {})))
            await db.commit()

    async def compact_events(self, retention_days: int, retention_floor: int) -> int:
        cutoff = (utc_now() - timedelta(days=retention_days)).isoformat()
        async with self.connect() as db:
            row = await (await db.execute("SELECT COALESCE(MIN(acknowledged_cursor),0) AS cursor FROM devices WHERE revoked_at IS NULL")).fetchone()
            latest = await (await db.execute("SELECT COALESCE(MAX(cursor),0) AS cursor FROM events")).fetchone()
            floor_cursor = max(0, latest["cursor"] - retention_floor)
            result = await db.execute("DELETE FROM events WHERE cursor<=? AND cursor<? AND timestamp<?", (row["cursor"], floor_cursor, cutoff))
            await db.commit()
            return result.rowcount

    async def observe_session(self, session_id: str, updated_at: str) -> bool:
        async with self.connect() as db:
            row = await (await db.execute("SELECT upstream_updated_at FROM observed_sessions WHERE session_id=?", (session_id,))).fetchone()
            if row and row["upstream_updated_at"] == updated_at:
                return False
            await db.execute("INSERT INTO observed_sessions VALUES(?,?) ON CONFLICT(session_id) DO UPDATE SET upstream_updated_at=excluded.upstream_updated_at", (session_id, updated_at))
            await db.commit()
            return True

    async def enqueue_prompt(self, session_id: str, item: dict[str, Any]) -> int:
        async with self.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            row = await (await db.execute("SELECT queued_prompts_json FROM session_state WHERE session_id=?", (session_id,))).fetchone()
            queue = json.loads(row["queued_prompts_json"]) if row else []
            queue.append(item)
            await db.execute(
                "INSERT INTO session_state(session_id,queued_prompts_json,updated_at) VALUES(?,?,?) ON CONFLICT(session_id) DO UPDATE SET queued_prompts_json=excluded.queued_prompts_json,updated_at=excluded.updated_at",
                (session_id, json.dumps(queue), utc_now().isoformat()),
            )
            await db.commit()
            return len(queue)

    async def dequeue_prompt(self, session_id: str) -> dict[str, Any] | None:
        async with self.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            row = await (await db.execute("SELECT queued_prompts_json FROM session_state WHERE session_id=?", (session_id,))).fetchone()
            queue = json.loads(row["queued_prompts_json"]) if row else []
            if not queue:
                await db.rollback()
                return None
            item = queue.pop(0)
            await db.execute("UPDATE session_state SET queued_prompts_json=?,updated_at=? WHERE session_id=?", (json.dumps(queue), utc_now().isoformat(), session_id))
            await db.commit()
            return item
