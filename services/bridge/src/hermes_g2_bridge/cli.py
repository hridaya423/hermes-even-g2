import argparse
import asyncio
import json
import logging
from typing import Any

import uvicorn

from .app import create_app
from .config import Settings
from .hermes import HermesClient
from .store import Store

logger = logging.getLogger(__name__)


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
