from pathlib import Path

import pytest

from hermes_g2_bridge.cli import build_doctor_report
from hermes_g2_bridge.config import Settings


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
