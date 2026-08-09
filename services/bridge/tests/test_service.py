from pathlib import Path

import pytest

from hermes_g2_bridge.service import ControlService
from hermes_g2_bridge.store import Store


class RecoveringHermes:
    def __init__(self):
        self.probe_calls = 0

    async def probe(self):
        self.probe_calls += 1
        return {
            "healthy": True,
            "detailed": {"gui_ready": False},
            "nativeSessions": True,
            "sessionHistory": True,
            "sessionStreaming": True,
            "sessionRunControl": True,
            "sessionApprovalResponse": True,
        }


@pytest.mark.asyncio
async def test_ensure_core_reprobes_after_hermes_restart(tmp_path: Path):
    hermes = RecoveringHermes()
    service = ControlService(
        Store(tmp_path / "bridge.db"),
        hermes,
        action_max_age_seconds=300,
        summary_helper=tmp_path / "summary-helper",
        whisper_binary=tmp_path / "whisper",
        whisper_model=tmp_path / "model",
        tailscale_cli=tmp_path / "tailscale",
    )
    service.runtime["coreReady"] = False

    await service.ensure_core()

    assert hermes.probe_calls == 1
    assert service.runtime["coreReady"] is True
