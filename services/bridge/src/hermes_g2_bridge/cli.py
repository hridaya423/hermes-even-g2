import argparse
import asyncio
import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

import uvicorn

from .app import create_app
from .config import Settings
from .hermes import HermesClient
from .store import Store

logger = logging.getLogger(__name__)

RESTORABLE_TABLES = (
    "events",
    "session_state",
    "run_correlation",
    "observed_sessions",
    "audit_log",
    "summary_cache",
)
SENSITIVE_TABLES = ("devices", "pairing_codes", "idempotency", "attachments")


def _integrity_check(connection: sqlite3.Connection) -> None:
    result = connection.execute("PRAGMA integrity_check").fetchone()
    if not result or result[0] != "ok":
        raise ValueError("SQLite integrity check failed")


def backup_state(database: Path, destination: Path) -> dict[str, Any]:
    database = database.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if database == destination:
        raise ValueError("backup destination must differ from the live database")
    if not database.is_file():
        raise ValueError("live bridge database does not exist")
    if destination.exists():
        raise ValueError("backup destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sqlite3.connect(database) as source, sqlite3.connect(destination) as target:
            source.backup(target)
            for table in SENSITIVE_TABLES:
                target.execute(f"DELETE FROM {table}")
            target.commit()
            _integrity_check(target)
            target.execute("VACUUM")
        os.chmod(destination, 0o600)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return {
        "status": "created",
        "path": str(destination),
        "credentialsIncluded": False,
        "mode": "0600",
    }


def restore_state(database: Path, source: Path) -> dict[str, Any]:
    database = database.expanduser().resolve()
    source = source.expanduser().resolve()
    if database == source:
        raise ValueError("restore source must differ from the live database")
    if not database.is_file() or not source.is_file():
        raise ValueError("live database and restore source must both exist")
    with sqlite3.connect(source) as candidate:
        _integrity_check(candidate)
        tables = {
            row[0]
            for row in candidate.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        required = set(RESTORABLE_TABLES) | set(SENSITIVE_TABLES)
        if not required.issubset(tables):
            raise ValueError("restore source is not a compatible Hermes G2 backup")
        if any(candidate.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in SENSITIVE_TABLES):
            raise ValueError("restore source contains device credentials or private pending state")

    with sqlite3.connect(database) as target:
        target.execute("PRAGMA foreign_keys=ON")
        target.execute("ATTACH DATABASE ? AS restored", (str(source),))
        try:
            target.execute("BEGIN IMMEDIATE")
            for table in RESTORABLE_TABLES:
                live_columns = [row[1] for row in target.execute(f"PRAGMA main.table_info({table})")]
                restored_columns = [row[1] for row in target.execute(f"PRAGMA restored.table_info({table})")]
                if live_columns != restored_columns:
                    raise ValueError(f"restore source schema mismatch for {table}")
                target.execute(f"DELETE FROM main.{table}")
                target.execute(f"INSERT INTO main.{table} SELECT * FROM restored.{table}")
            target.commit()
            _integrity_check(target)
        except Exception:
            target.rollback()
            raise
        finally:
            target.execute("DETACH DATABASE restored")
    return {"status": "restored", "path": str(source), "credentialsPreserved": True}


async def build_doctor_report(settings: Settings, client: HermesClient | None = None) -> dict[str, Any]:
    hermes = client or HermesClient(
        settings.hermes_origin,
        settings.hermes_api_key.get_secret_value(),
    )
    capabilities: dict[str, Any] = {}
    hermes_state = "unreachable"
    try:
        capabilities = await hermes.probe()
        hermes_state = "ready"
    except Exception as error:
        logger.debug("Hermes doctor probe failed: %s", type(error).__name__)
    finally:
        await hermes.close()

    core_ready = all(
        capabilities.get(name)
        for name in ("nativeSessions", "sessionHistory", "sessionStreaming")
    )
    detailed = capabilities.get("detailed", {})
    gui_ready = bool(detailed.get("gui_ready", detailed.get("guiReady", False)))
    database_state = "ready" if settings.database_path.parent.exists() else "missing"
    stt_state = (
        "ready"
        if settings.whisper_binary.is_file()
        and settings.whisper_binary.stat().st_mode & 0o111
        and settings.whisper_model.is_file()
        else "missing"
    )
    tailscale_state = "missing"
    if settings.tailscale_cli.is_file():
        try:
            process = await asyncio.create_subprocess_exec(
                str(settings.tailscale_cli),
                "status",
                "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=5)
            payload = json.loads(stdout) if process.returncode == 0 else {}
            tailscale_state = (
                "ready" if payload.get("BackendState") == "Running" else "not_running"
            )
        except (OSError, TimeoutError, json.JSONDecodeError):
            tailscale_state = "error"

    checks = {
        "database": database_state,
        "stt": stt_state,
        "tailscale": tailscale_state,
        "hermes": hermes_state,
    }
    return {
        "ok": core_ready and all(value == "ready" for value in checks.values()),
        "coreReady": core_ready,
        "guiReady": gui_ready,
        "checks": checks,
        "hermes": capabilities,
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="hermes-g2-bridge")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve")
    commands.add_parser("migrate")
    pair = commands.add_parser("pair")
    pair.add_argument("kind", choices=("hub", "android", "simulator"))
    commands.add_parser("doctor")
    backup = commands.add_parser("backup")
    backup.add_argument("--output", type=Path, required=True)
    restore = commands.add_parser("restore")
    restore.add_argument("--input", type=Path, required=True)
    restore.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    settings = Settings()
    store = Store(settings.database_path)
    if args.command == "serve":
        uvicorn.run(create_app(settings), host=settings.bind_host, port=settings.bind_port, access_log=False)
    elif args.command == "migrate":
        asyncio.run(store.migrate())
    elif args.command == "pair":
        async def make_pairing():
            await store.migrate()
            print(await store.create_pairing(args.kind, settings.pairing_ttl_seconds))
        asyncio.run(make_pairing())
    elif args.command == "doctor":
        async def doctor():
            report = await build_doctor_report(settings)
            print(json.dumps(report, indent=2))
            return 0 if report["ok"] else 1
        raise SystemExit(asyncio.run(doctor()))
    elif args.command == "backup":
        asyncio.run(store.migrate())
        print(json.dumps(backup_state(settings.database_path, args.output), indent=2))
    elif args.command == "restore":
        if not args.confirm:
            parser.error("restore requires --confirm after the bridge service is stopped")
        asyncio.run(store.migrate())
        print(json.dumps(restore_state(settings.database_path, args.input), indent=2))
