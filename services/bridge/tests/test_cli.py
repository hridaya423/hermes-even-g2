import sqlite3
from pathlib import Path

import pytest

from hermes_g2_bridge.cli import backup_state, build_doctor_report, restore_state
from hermes_g2_bridge.config import Settings
from hermes_g2_bridge.models import EventInput
from hermes_g2_bridge.store import Store


class HealthyHermes:
    async def probe(self):
        return {
            "nativeSessions": True,
            "sessionHistory": True,
            "sessionStreaming": True,
            "detailed": {"guiReady": False},
        }

    async def close(self):
        pass


class UnreachableHermes:
    async def probe(self):
        raise RuntimeError("secret-token must never escape")

    async def close(self):
        pass


def settings(tmp_path: Path) -> Settings:
    database = tmp_path / "state" / "bridge.db"
    database.parent.mkdir()
    whisper = tmp_path / "whisper-cli"
    whisper.write_text("")
    whisper.chmod(0o755)
    model = tmp_path / "model.bin"
    model.write_text("model")
    return Settings(
        hermes_api_key="secret-token",
        database_path=database,
        whisper_binary=whisper,
        whisper_model=model,
        tailscale_cli=tmp_path / "missing-tailscale",
    )


@pytest.mark.asyncio
async def test_doctor_reports_core_and_gui_readiness_separately(tmp_path):
    report = await build_doctor_report(settings(tmp_path), HealthyHermes())

    assert report["ok"] is False
    assert report["coreReady"] is True
    assert report["guiReady"] is False
    assert report["checks"]["stt"] == "ready"
    assert report["checks"]["tailscale"] == "missing"


@pytest.mark.asyncio
async def test_doctor_fails_closed_without_leaking_exception_or_key(tmp_path):
    report = await build_doctor_report(settings(tmp_path), UnreachableHermes())

    assert report["ok"] is False
    assert report["coreReady"] is False
    assert report["checks"]["hermes"] == "unreachable"
    assert "secret-token" not in str(report)


@pytest.mark.asyncio
async def test_backup_excludes_credentials_and_restore_preserves_local_devices(tmp_path):
    database = tmp_path / "bridge.db"
    backup = tmp_path / "state-backup.db"
    store = Store(database)
    await store.migrate()
    code = await store.create_pairing("android", 90)
    device_id, credential, _ = await store.exchange_pairing(code, "phone", "android")
    await store.append_event(EventInput(kind="session.updated", source="bridge", sessionId="session-a", payload={"state": "idle"}))

    report = backup_state(database, backup)

    assert report["credentialsIncluded"] is False
    assert backup.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backup) as connection:
        assert connection.execute("SELECT COUNT(*) FROM devices").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM pairing_codes").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM idempotency").fetchone()[0] == 0

    await store.append_event(EventInput(kind="run.failed", source="bridge", sessionId="session-b", payload={"error": "later"}))
    restored = restore_state(database, backup)

    assert restored["credentialsPreserved"] is True
    assert await store.authenticate(device_id, credential)
    events = await store.events_after(0)
    assert [(event["kind"], event["sessionId"]) for event in events] == [("session.updated", "session-a")]


@pytest.mark.asyncio
async def test_restore_rejects_a_backup_containing_device_credentials(tmp_path):
    database = tmp_path / "bridge.db"
    unsafe = tmp_path / "unsafe.db"
    store = Store(database)
    await store.migrate()
    code = await store.create_pairing("android", 90)
    await store.exchange_pairing(code, "phone", "android")
    with sqlite3.connect(database) as source, sqlite3.connect(unsafe) as destination:
        source.backup(destination)

    with pytest.raises(ValueError, match="contains device credentials"):
        restore_state(database, unsafe)
