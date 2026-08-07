import argparse
import asyncio
import json

import uvicorn

from .app import create_app
from .config import Settings
from .hermes import HermesClient
from .store import Store


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
            client = HermesClient(settings.hermes_origin, settings.hermes_api_key.get_secret_value())
            try:
                capabilities = await client.probe()
                print(json.dumps({"databaseParentExists": settings.database_path.parent.exists(), "sttBinary": settings.whisper_binary.exists(), "sttModel": settings.whisper_model.exists(), "hermes": capabilities}, indent=2))
            finally:
                await client.close()
        asyncio.run(doctor())

