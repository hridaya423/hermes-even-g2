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
from argon2.exceptions import VerificationError, VerifyMismatchError

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
    CREATE TABLE IF NOT EXISTS summary_cache(
      content_hash TEXT PRIMARY KEY, summary_json TEXT NOT NULL, created_at TEXT NOT NULL
    );
    """,
    """
    ALTER TABLE session_state ADD COLUMN source_override TEXT;
    """,
    """
    CREATE TABLE IF NOT EXISTS attachments(
      id TEXT PRIMARY KEY, device_id TEXT NOT NULL, session_id TEXT NOT NULL,
      name TEXT NOT NULL, media_type TEXT NOT NULL, path TEXT NOT NULL,
      sha256 TEXT NOT NULL, size INTEGER NOT NULL, created_at TEXT NOT NULL,
      consumed_at TEXT
    );
    CREATE INDEX IF NOT EXISTS attachments_session ON attachments(session_id, created_at);
    """,
    """
    ALTER TABLE attachments ADD COLUMN expires_at TEXT;
    UPDATE attachments SET expires_at=strftime('%Y-%m-%dT%H:%M:%f+00:00', created_at, '+1 day') WHERE expires_at IS NULL;
    CREATE INDEX IF NOT EXISTS attachments_expiry ON attachments(expires_at);
    """,
    """
    CREATE TABLE IF NOT EXISTS prompt_queue(
      id TEXT PRIMARY KEY, session_id TEXT NOT NULL, device_id TEXT NOT NULL,
      payload_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
      created_at TEXT NOT NULL, claimed_at TEXT, claim_token TEXT,
      updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS prompt_queue_session_status ON prompt_queue(session_id, status, created_at);
    """,
]


SCOPES = {
    "hub": ["sessions:read", "sessions:write", "audio:write", "runs:control", "approvals:write"],
    "android": ["sessions:read", "sessions:write", "audio:write", "runs:control", "approvals:write", "jobs:write", "attachments:write", "devices:manage", "diagnostics:read"],
    "simulator": ["sessions:read", "sessions:write", "audio:write", "runs:control", "approvals:write", "diagnostics:read"],
}


class Store:
    def __init__(self, path: Path, attachments_root: Path | None = None):
        self.path = path
        self.attachments_root = attachments_root
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
            await db.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY)"
            )
            applied = {
                row["version"]
                for row in await db.execute_fetchall("SELECT version FROM schema_migrations")
            }
            for index, sql in enumerate(MIGRATIONS, start=1):
                if index in applied:
                    continue
                if index == 2:
                    columns = {
                        row["name"]
                        for row in await db.execute_fetchall("PRAGMA table_info(session_state)")
                    }
                    if "source_override" in columns:
                        await db.execute(
                            "INSERT OR IGNORE INTO schema_migrations(version) VALUES(?)",
                            (index,),
                        )
                        continue
                if index == 4:
                    columns = {
                        row["name"]
                        for row in await db.execute_fetchall("PRAGMA table_info(attachments)")
                    }
                    if "expires_at" in columns:
                        await db.execute(
                            "INSERT OR IGNORE INTO schema_migrations(version) VALUES(?)",
                            (index,),
                        )
                        continue
                if index == 5:
                    await db.executescript(sql)
                    columns = {
                        row["name"]
                        for row in await db.execute_fetchall("PRAGMA table_info(session_state)")
                    }
                    if "active_admission_id" not in columns:
                        await db.execute("ALTER TABLE session_state ADD COLUMN active_admission_id TEXT")
                    if "active_admission_at" not in columns:
                        await db.execute("ALTER TABLE session_state ADD COLUMN active_admission_at TEXT")
                    await db.execute(
                        "INSERT OR IGNORE INTO schema_migrations(version) VALUES(?)", (index,)
                    )
                    continue
                await db.executescript(sql)
                await db.execute(
                    "INSERT OR IGNORE INTO schema_migrations(version) VALUES(?)", (index,)
                )
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
        except (VerificationError, VerifyMismatchError):
            return None
        return {"id": row["id"], "kind": row["kind"], "scopes": json.loads(row["scopes_json"])}

    async def is_device_active(self, device_id: str) -> bool:
        async with self.connect() as db:
            row = await (await db.execute(
                "SELECT revoked_at,expires_at FROM devices WHERE id=?", (device_id,)
            )).fetchone()
        return bool(
            row
            and row["revoked_at"] is None
            and (not row["expires_at"] or row["expires_at"] > utc_now().isoformat())
        )

    async def device_exists(self, device_id: str) -> bool:
        async with self.connect() as db:
            row = await (await db.execute("SELECT 1 FROM devices WHERE id=?", (device_id,))).fetchone()
        return row is not None

    async def revoke_device(self, device_id: str) -> bool:
        async with self.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            result = await db.execute(
                "UPDATE devices SET revoked_at=COALESCE(revoked_at,?) WHERE id=?",
                (utc_now().isoformat(), device_id),
            )
            await db.commit()
        async with self._condition:
            self._condition.notify_all()
        return result.rowcount > 0

    async def record_attachment(
        self,
        attachment_id: str,
        device_id: str,
        session_id: str,
        name: str,
        media_type: str,
        path: Path,
        digest: str,
        size: int,
        *,
        ttl_seconds: int = 24 * 60 * 60,
        device_quota_bytes: int | None = None,
        total_quota_bytes: int | None = None,
    ) -> None:
        expires_at = (utc_now() + timedelta(seconds=ttl_seconds)).isoformat()
        async with self.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            device_usage = await (await db.execute(
                "SELECT COALESCE(SUM(size),0) usage FROM attachments WHERE device_id=?",
                (device_id,),
            )).fetchone()
            total_usage = await (await db.execute(
                "SELECT COALESCE(SUM(size),0) usage FROM attachments"
            )).fetchone()
            if device_quota_bytes is not None and device_usage["usage"] + size > device_quota_bytes:
                await db.rollback()
                raise ValueError("device attachment quota exceeded")
            if total_quota_bytes is not None and total_usage["usage"] + size > total_quota_bytes:
                await db.rollback()
                raise ValueError("bridge attachment quota exceeded")
            await db.execute(
                "INSERT INTO attachments(id,device_id,session_id,name,media_type,path,sha256,size,created_at,consumed_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,NULL,?)",
                (
                    attachment_id,
                    device_id,
                    session_id,
                    name,
                    media_type,
                    str(path),
                    digest,
                    size,
                    utc_now().isoformat(),
                    expires_at,
                ),
            )
            await db.commit()

    async def cleanup_attachments(self) -> int:
        now = utc_now().isoformat()
        async with self.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            rows = await db.execute_fetchall(
                "SELECT id,path FROM attachments WHERE expires_at IS NOT NULL AND expires_at<=?",
                (now,),
            )
            if rows:
                placeholders = ",".join("?" for _ in rows)
                await db.execute(
                    f"DELETE FROM attachments WHERE id IN ({placeholders})",
                    [row["id"] for row in rows],
                )
            known_paths = {
                str(Path(row["path"]).resolve())
                for row in await db.execute_fetchall("SELECT path FROM attachments")
            }
            await db.commit()
        for row in rows:
            Path(row["path"]).unlink(missing_ok=True)
        orphan_count = 0
        if self.attachments_root and self.attachments_root.exists():
            root = self.attachments_root.resolve()
            for path in self.attachments_root.rglob("*"):
                if not path.is_file():
                    continue
                resolved = path.resolve()
                if root not in resolved.parents:
                    continue
                if str(resolved) not in known_paths:
                    path.unlink(missing_ok=True)
                    orphan_count += 1
            for directory in sorted(self.attachments_root.rglob("*"), reverse=True):
                if directory.is_dir():
                    try:
                        directory.rmdir()
                    except OSError:
                        pass
        return len(rows) + orphan_count

    async def delete_consumed_attachments(
        self, session_id: str, attachment_ids: list[str]
    ) -> int:
        if not attachment_ids:
            return 0
        placeholders = ",".join("?" for _ in attachment_ids)
        async with self.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            rows = await db.execute_fetchall(
                f"SELECT id,path FROM attachments WHERE session_id=? AND consumed_at IS NOT NULL "
                f"AND id IN ({placeholders})",
                [session_id, *attachment_ids],
            )
            if rows:
                claimed_placeholders = ",".join("?" for _ in rows)
                await db.execute(
                    f"DELETE FROM attachments WHERE id IN ({claimed_placeholders})",
                    [row["id"] for row in rows],
                )
            await db.commit()
        for row in rows:
            Path(row["path"]).unlink(missing_ok=True)
        return len(rows)

    async def claim_attachments(
        self,
        device_id: str,
        session_id: str,
        attachment_ids: list[str],
    ) -> list[dict[str, Any]]:
        if not attachment_ids:
            return []
        if len(attachment_ids) != len(set(attachment_ids)):
            raise ValueError("attachment IDs must be unique")
        placeholders = ",".join("?" for _ in attachment_ids)
        async with self.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            rows = await db.execute_fetchall(
                f"SELECT * FROM attachments WHERE id IN ({placeholders}) "
                "AND device_id=? AND session_id=? AND consumed_at IS NULL "
                "AND (expires_at IS NULL OR expires_at>?)",
                [*attachment_ids, device_id, session_id, utc_now().isoformat()],
            )
            by_id = {row["id"]: row for row in rows}
            if len(by_id) != len(attachment_ids) or any(
                not Path(by_id[attachment_id]["path"]).is_file()
                for attachment_id in attachment_ids
                if attachment_id in by_id
            ):
                await db.rollback()
                raise ValueError("one or more attachments are not available for this device and session")
            claimed_at = utc_now().isoformat()
            await db.execute(
                f"UPDATE attachments SET consumed_at=? WHERE id IN ({placeholders})",
                [claimed_at, *attachment_ids],
            )
            await db.commit()
        return [
            {
                "attachmentId": row["id"],
                "name": row["name"],
                "mediaType": row["media_type"],
                "path": row["path"],
                "sha256": row["sha256"],
                "size": row["size"],
            }
            for row in (by_id[attachment_id] for attachment_id in attachment_ids)
        ]

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

    async def event_bounds(self) -> tuple[int, int]:
        async with self.connect() as db:
            row = await (await db.execute(
                "SELECT COALESCE(MIN(cursor),0) oldest, COALESCE(MAX(cursor),0) latest FROM events"
            )).fetchone()
        return int(row["oldest"]), int(row["latest"])

    async def event_stream(
        self, after: int, *, device_id: str | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        cursor = after
        if device_id is not None and not await self.is_device_active(device_id):
            yield {"type": "auth.revoked", "deviceId": device_id}
            return
        oldest, latest = await self.event_bounds()
        if after > 0 and oldest > 0 and after < oldest - 1:
            yield {
                "type": "replay.gap",
                "cursor": after,
                "oldestCursor": oldest,
                "latestCursor": latest,
                "requiresSnapshot": True,
            }
        while True:
            if device_id is not None and not await self.is_device_active(device_id):
                yield {"type": "auth.revoked", "deviceId": device_id}
                return
            events = await self.events_after(cursor)
            if events:
                for event in events:
                    if device_id is not None and not await self.is_device_active(device_id):
                        yield {"type": "auth.revoked", "deviceId": device_id}
                        return
                    cursor = event["cursor"]
                    yield event
                continue
            async with self._condition:
                try:
                    await asyncio.wait_for(self._condition.wait(), timeout=2)
                except TimeoutError:
                    yield {"type": "keepalive", "cursor": cursor}

    async def idempotency_begin(
        self, device_id: str, key: str, body: bytes
    ) -> tuple[bool, int | None, dict[str, Any] | None]:
        request_hash = hashlib.sha256(body).hexdigest()
        async with self.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            row = await (await db.execute("SELECT * FROM idempotency WHERE device_id=? AND idempotency_key=?", (device_id, key))).fetchone()
            if row:
                await db.rollback()
                if row["request_hash"] != request_hash:
                    raise ValueError("idempotency key was already used with another request body")
                return (
                    False,
                    row["status_code"],
                    json.loads(row["response_json"]) if row["response_json"] else None,
                )
            await db.execute("INSERT INTO idempotency VALUES(?,?,?,?,?,?)", (device_id, key, request_hash, None, None, utc_now().isoformat()))
            await db.commit()
            return True, None, None

    async def idempotency_finish(self, device_id: str, key: str, status: int, response: dict[str, Any]) -> None:
        async with self.connect() as db:
            await db.execute("UPDATE idempotency SET status_code=?, response_json=? WHERE device_id=? AND idempotency_key=?", (status, json.dumps(response), device_id, key))
            await db.commit()

    async def acknowledge(self, device_id: str, cursor: int) -> None:
        async with self.connect() as db:
            await db.execute("UPDATE devices SET acknowledged_cursor=MAX(acknowledged_cursor,?) WHERE id=?", (cursor, device_id))
            await db.commit()

    async def approval_is_pending(self, session_id: str, run_id: str, request_id: str | None) -> bool:
        async with self.connect() as db:
            rows = await db.execute_fetchall(
                "SELECT kind,payload_json FROM events WHERE session_id=? AND run_id=? "
                "AND kind IN ('approval.required','approval.resolved') ORDER BY cursor",
                (session_id, run_id),
            )
        pending = False
        for row in rows:
            payload = json.loads(row["payload_json"])
            if request_id and payload.get("requestId") != request_id:
                continue
            pending = row["kind"] == "approval.required"
        return pending

    async def cached_summary(self, content_hash: str) -> dict[str, Any] | None:
        async with self.connect() as db:
            row = await (await db.execute(
                "SELECT summary_json FROM summary_cache WHERE content_hash=?",
                (content_hash,),
            )).fetchone()
        return json.loads(row["summary_json"]) if row else None

    async def cache_summary(self, content_hash: str, summary: dict[str, Any]) -> None:
        async with self.connect() as db:
            await db.execute(
                "INSERT OR REPLACE INTO summary_cache VALUES(?,?,?)",
                (content_hash, json.dumps(summary, separators=(",", ":")), utc_now().isoformat()),
            )
            await db.commit()

    async def session_overlays(self, session_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not session_ids:
            return {}
        placeholders = ",".join("?" for _ in session_ids)
        async with self.connect() as db:
            # Only the placeholder count is interpolated; every session ID remains parameterized.
            state_rows = await db.execute_fetchall(
                f"SELECT session_id,pinned,queued_prompts_json,source_override,active_run_id,active_admission_id FROM session_state WHERE session_id IN ({placeholders})",
                session_ids,
            )
            lifecycle_rows = await db.execute_fetchall(
                f"SELECT e.session_id,e.kind FROM events e JOIN (SELECT session_id,MAX(cursor) cursor FROM events WHERE session_id IN ({placeholders}) AND kind IN ('run.started','run.completed','run.failed','run.cancelled') GROUP BY session_id) latest ON e.cursor=latest.cursor",
                session_ids,
            )
            message_rows = await db.execute_fetchall(
                f"SELECT e.session_id,e.payload_json FROM events e JOIN (SELECT session_id,MAX(cursor) cursor FROM events WHERE session_id IN ({placeholders}) AND kind='message.completed' GROUP BY session_id) latest ON e.cursor=latest.cursor",
                session_ids,
            )
        overlays = {session_id: {"pinned": False, "state": "idle"} for session_id in session_ids}
        for row in state_rows:
            overlays[row["session_id"]]["pinned"] = bool(row["pinned"])
            if row["source_override"]:
                overlays[row["session_id"]]["source"] = row["source_override"]
            if row["active_run_id"] or row["active_admission_id"]:
                overlays[row["session_id"]]["state"] = "busy"
            elif json.loads(row["queued_prompts_json"]):
                overlays[row["session_id"]]["state"] = "queued"
        for row in lifecycle_rows:
            if overlays[row["session_id"]]["state"] != "queued":
                overlays[row["session_id"]]["state"] = "busy" if row["kind"] == "run.started" else "idle"
        for row in message_rows:
            payload = json.loads(row["payload_json"])
            overlays[row["session_id"]]["latestAnswer"] = (
                payload.get("content") or payload.get("message") or payload.get("summary")
            )
        return overlays

    async def set_session_source(self, session_id: str, source: str) -> None:
        async with self.connect() as db:
            await db.execute(
                "INSERT INTO session_state(session_id,source_override,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(session_id) DO UPDATE SET source_override=excluded.source_override,"
                "updated_at=excluded.updated_at",
                (session_id, source, utc_now().isoformat()),
            )
            await db.commit()

    async def session_is_busy(self, session_id: str) -> bool:
        overlay = (await self.session_overlays([session_id])).get(session_id, {})
        return overlay.get("state") in {"busy", "queued"}

    async def session_turn_active(self, session_id: str) -> bool:
        async with self.connect() as db:
            state = await (await db.execute(
                "SELECT active_run_id,active_admission_id FROM session_state WHERE session_id=?",
                (session_id,),
            )).fetchone()
            if state and (state["active_run_id"] or state["active_admission_id"]):
                return True
            row = await (await db.execute(
                "SELECT kind FROM events WHERE session_id=? AND kind IN "
                "('run.started','run.completed','run.failed','run.cancelled') "
                "ORDER BY cursor DESC LIMIT 1",
                (session_id,),
            )).fetchone()
        return bool(row and row["kind"] == "run.started")

    async def run_is_active(self, session_id: str, run_id: str) -> bool:
        async with self.connect() as db:
            row = await (await db.execute(
                "SELECT 1 FROM run_correlation WHERE session_id=? AND run_id=? "
                "AND status NOT IN ('completed','failed','cancelled')",
                (session_id, run_id),
            )).fetchone()
        return row is not None

    async def update_run(
        self,
        run_id: str,
        session_id: str,
        device_id: str,
        status: str,
        initiated_by_g2: bool,
    ) -> None:
        async with self.connect() as db:
            await db.execute(
                "INSERT INTO run_correlation(run_id,session_id,device_id,initiated_by_g2,status,updated_at) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET "
                "status=excluded.status,updated_at=excluded.updated_at",
                (
                    run_id,
                    session_id,
                    device_id,
                    int(initiated_by_g2),
                    status,
                    utc_now().isoformat(),
                ),
            )
            active_run = None if status in {"completed", "failed", "cancelled"} else run_id
            await db.execute(
                "INSERT INTO session_state(session_id,active_run_id,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(session_id) DO UPDATE SET active_run_id=excluded.active_run_id,"
                "updated_at=excluded.updated_at",
                (session_id, active_run, utc_now().isoformat()),
            )
            await db.commit()

    async def bind_admission(
        self,
        admission_id: str,
        queue_id: str | None,
        session_id: str,
        run_id: str,
        device_id: str,
    ) -> None:
        """Convert a durable prompt admission into the provider run atomically."""
        async with self.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            state = await (await db.execute(
                "SELECT active_admission_id FROM session_state WHERE session_id=?",
                (session_id,),
            )).fetchone()
            if not state or state["active_admission_id"] != admission_id:
                await db.rollback()
                raise ValueError("prompt admission is stale or already bound")
            await db.execute(
                "INSERT INTO run_correlation(run_id,session_id,device_id,initiated_by_g2,status,updated_at) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(run_id) DO UPDATE SET session_id=excluded.session_id,device_id=excluded.device_id,status=excluded.status,updated_at=excluded.updated_at",
                (run_id, session_id, device_id, 1, "started", utc_now().isoformat()),
            )
            await db.execute(
                "UPDATE session_state SET active_run_id=?,active_admission_id=NULL,active_admission_at=NULL,updated_at=? WHERE session_id=? AND active_admission_id=?",
                (run_id, utc_now().isoformat(), session_id, admission_id),
            )
            if queue_id:
                await db.execute(
                    "UPDATE prompt_queue SET status='running',updated_at=? WHERE id=? AND claim_token=?",
                    (utc_now().isoformat(), queue_id, admission_id),
                )
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
        queue_id = str(uuid.uuid4())
        now = utc_now().isoformat()
        async with self.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            row = await (await db.execute("SELECT queued_prompts_json FROM session_state WHERE session_id=?", (session_id,))).fetchone()
            queue = json.loads(row["queued_prompts_json"]) if row else []
            queue.append(item)
            await db.execute(
                "INSERT INTO prompt_queue(id,session_id,device_id,payload_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (queue_id, session_id, str(item.get("deviceId", "")), json.dumps(item), "queued", now, now),
            )
            await db.execute(
                "INSERT INTO session_state(session_id,queued_prompts_json,updated_at) VALUES(?,?,?) ON CONFLICT(session_id) DO UPDATE SET queued_prompts_json=excluded.queued_prompts_json,updated_at=excluded.updated_at",
                (session_id, json.dumps(queue), now),
            )
            await db.commit()
            return len(queue)

    async def dequeue_prompt(self, session_id: str) -> dict[str, Any] | None:
        """Legacy destructive dequeue retained for compatibility with older callers."""
        async with self.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            row = await (await db.execute("SELECT queued_prompts_json FROM session_state WHERE session_id=?", (session_id,))).fetchone()
            queue = json.loads(row["queued_prompts_json"]) if row else []
            if not queue:
                await db.rollback()
                return None
            item = queue.pop(0)
            queue_row = await (await db.execute(
                "SELECT id FROM prompt_queue WHERE session_id=? AND status='queued' ORDER BY created_at LIMIT 1",
                (session_id,),
            )).fetchone()
            if queue_row:
                await db.execute("DELETE FROM prompt_queue WHERE id=?", (queue_row["id"],))
            await db.execute("UPDATE session_state SET queued_prompts_json=?,updated_at=? WHERE session_id=?", (json.dumps(queue), utc_now().isoformat(), session_id))
            await db.commit()
            return item

    async def admit_prompt(
        self, session_id: str, item: dict[str, Any], *, force_queue: bool = False
    ) -> dict[str, Any]:
        """Persist a prompt before admitting it, atomically serializing one turn per session."""
        queue_id = str(uuid.uuid4())
        admission_id = str(uuid.uuid4())
        now = utc_now().isoformat()
        async with self.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            state = await (await db.execute(
                "SELECT active_run_id,active_admission_id,queued_prompts_json FROM session_state WHERE session_id=?",
                (session_id,),
            )).fetchone()
            latest = await (await db.execute(
                "SELECT kind FROM events WHERE session_id=? AND kind IN ('run.started','run.completed','run.failed','run.cancelled') ORDER BY cursor DESC LIMIT 1",
                (session_id,),
            )).fetchone()
            busy = bool(
                force_queue
                or (state and (state["active_run_id"] or state["active_admission_id"]))
                or (latest and latest["kind"] == "run.started")
            )
            status = "queued" if busy else "admitted"
            await db.execute(
                "INSERT INTO prompt_queue(id,session_id,device_id,payload_json,status,created_at,claimed_at,claim_token,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (queue_id, session_id, str(item.get("deviceId", "")), json.dumps(item), status, now, now if not busy else None, admission_id if not busy else None, now),
            )
            if busy:
                queue = json.loads(state["queued_prompts_json"]) if state else []
                queue.append(item)
                await db.execute(
                    "INSERT INTO session_state(session_id,queued_prompts_json,updated_at) VALUES(?,?,?) ON CONFLICT(session_id) DO UPDATE SET queued_prompts_json=excluded.queued_prompts_json,updated_at=excluded.updated_at",
                    (session_id, json.dumps(queue), now),
                )
                await db.commit()
                return {"status": "queued", "queueId": queue_id, "position": len(queue)}
            await db.execute(
                "INSERT INTO session_state(session_id,active_admission_id,active_admission_at,updated_at) VALUES(?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET active_admission_id=excluded.active_admission_id,active_admission_at=excluded.active_admission_at,updated_at=excluded.updated_at",
                (session_id, admission_id, now, now),
            )
            await db.commit()
            return {"status": "admitted", "queueId": queue_id, "admissionId": admission_id}

    async def claim_next_prompt(self, session_id: str) -> dict[str, Any] | None:
        """Claim one queued prompt without deleting it, so a crash cannot silently drop it."""
        async with self.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            while True:
                row = await (await db.execute(
                    "SELECT * FROM prompt_queue WHERE session_id=? AND status='queued' ORDER BY created_at LIMIT 1",
                    (session_id,),
                )).fetchone()
                if not row:
                    await db.rollback()
                    return None
                if not await self._device_active_in_db(db, row["device_id"]):
                    payload = json.loads(row["payload_json"])
                    await db.execute("DELETE FROM prompt_queue WHERE id=?", (row["id"],))
                    state = await (await db.execute("SELECT queued_prompts_json FROM session_state WHERE session_id=?", (session_id,))).fetchone()
                    queue = json.loads(state["queued_prompts_json"]) if state else []
                    if payload in queue:
                        queue.remove(payload)
                        await db.execute("UPDATE session_state SET queued_prompts_json=?,updated_at=? WHERE session_id=?", (json.dumps(queue), utc_now().isoformat(), session_id))
                    elif queue:
                        queue.pop(0)
                        await db.execute("UPDATE session_state SET queued_prompts_json=?,updated_at=? WHERE session_id=?", (json.dumps(queue), utc_now().isoformat(), session_id))
                    continue
                claim_token = str(uuid.uuid4())
                now = utc_now().isoformat()
                await db.execute(
                    "UPDATE prompt_queue SET status='claimed',claimed_at=?,claim_token=?,updated_at=? WHERE id=?",
                    (now, claim_token, now, row["id"]),
                )
                await db.execute(
                    "INSERT INTO session_state(session_id,active_admission_id,active_admission_at,updated_at) VALUES(?,?,?,?) "
                    "ON CONFLICT(session_id) DO UPDATE SET active_admission_id=excluded.active_admission_id,active_admission_at=excluded.active_admission_at,updated_at=excluded.updated_at",
                    (session_id, claim_token, now, now),
                )
                await db.commit()
                value = json.loads(row["payload_json"])
                return {**value, "queueId": row["id"], "claimToken": claim_token}

    async def complete_prompt(self, queue_id: str, session_id: str) -> None:
        async with self.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            row = await (await db.execute("SELECT payload_json,claim_token FROM prompt_queue WHERE id=?", (queue_id,))).fetchone()
            await db.execute("DELETE FROM prompt_queue WHERE id=?", (queue_id,))
            if row and row["claim_token"]:
                await db.execute(
                    "UPDATE session_state SET active_admission_id=NULL,active_admission_at=NULL,updated_at=? WHERE session_id=? AND active_admission_id=?",
                    (utc_now().isoformat(), session_id, row["claim_token"]),
                )
            state = await (await db.execute("SELECT queued_prompts_json FROM session_state WHERE session_id=?", (session_id,))).fetchone()
            queue = json.loads(state["queued_prompts_json"]) if state else []
            payload = json.loads(row["payload_json"]) if row else None
            if payload in queue:
                queue.remove(payload)
                await db.execute("UPDATE session_state SET queued_prompts_json=?,updated_at=? WHERE session_id=?", (json.dumps(queue), utc_now().isoformat(), session_id))
            elif queue and row:
                queue.pop(0)
                await db.execute("UPDATE session_state SET queued_prompts_json=?,updated_at=? WHERE session_id=?", (json.dumps(queue), utc_now().isoformat(), session_id))
            await db.commit()

    async def release_admission(self, session_id: str, admission_id: str) -> None:
        async with self.connect() as db:
            await db.execute(
                "UPDATE session_state SET active_admission_id=NULL,active_admission_at=NULL,updated_at=? WHERE session_id=? AND active_admission_id=?",
                (utc_now().isoformat(), session_id, admission_id),
            )
            await db.execute(
                "UPDATE prompt_queue SET status='interrupted',updated_at=? WHERE session_id=? AND claim_token=? AND status='admitted'",
                (utc_now().isoformat(), session_id, admission_id),
            )
            await db.commit()

    async def recover_prompt_admissions(self) -> list[dict[str, Any]]:
        """Mark pre-crash admissions interrupted; callers can surface a retry affordance."""
        async with self.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            rows = await db.execute_fetchall(
                "SELECT * FROM prompt_queue WHERE status IN ('admitted','claimed')"
            )
            if rows:
                await db.execute(
                    "UPDATE prompt_queue SET status='interrupted',updated_at=? WHERE status IN ('admitted','claimed')",
                    (utc_now().isoformat(),),
                )
                await db.execute(
                    "UPDATE session_state SET active_admission_id=NULL,active_admission_at=NULL,updated_at=? WHERE active_admission_id IS NOT NULL",
                    (utc_now().isoformat(),),
                )
            await db.commit()
        return [json.loads(row["payload_json"]) | {"queueId": row["id"]} for row in rows]

    async def list_queued_prompts(self, session_id: str) -> list[dict[str, Any]]:
        async with self.connect() as db:
            rows = await db.execute_fetchall(
                "SELECT payload_json,id,status FROM prompt_queue WHERE session_id=? AND status='queued' ORDER BY created_at",
                (session_id,),
            )
        return [json.loads(row["payload_json"]) | {"queueId": row["id"]} for row in rows]

    @staticmethod
    async def _device_active_in_db(db: aiosqlite.Connection, device_id: str) -> bool:
        row = await (await db.execute("SELECT revoked_at,expires_at FROM devices WHERE id=?", (device_id,))).fetchone()
        return bool(row and row["revoked_at"] is None and (not row["expires_at"] or row["expires_at"] > utc_now().isoformat()))
