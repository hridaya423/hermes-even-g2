import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from hermes_g2_bridge.app import create_app
from hermes_g2_bridge.config import Settings
from hermes_g2_bridge.models import EventInput


def configured_app(tmp_path):
    app = create_app(Settings(
        hermes_api_key="test-only-master-key",
        database_path=tmp_path / "bridge.db",
        whisper_binary=tmp_path / "whisper-cli",
        whisper_model=tmp_path / "model.bin",
    ))
    app.state.control.probe = AsyncMock(return_value={
        "runtime": {"bridge": True, "hermes": True, "coreReady": True, "guiReady": False},
        "hermes": {"nativeSessions": True, "sessionHistory": True, "sessionStreaming": True},
    })
    app.state.control.reconcile_once = AsyncMock(return_value=0)
    return app


def pair(client, app, kind="android"):
    code = client.portal.call(app.state.store.create_pairing, kind, 90)
    response = client.post("/v1/pairings/exchange", json={
        "code": code,
        "deviceName": "test device",
        "deviceKind": kind,
    })
    assert response.status_code == 200
    value = response.json()
    return value, {
        "Authorization": f"Bearer {value['credential']}",
        "X-Device-Id": value["deviceId"],
    }


def test_pairing_auth_and_idempotent_action(tmp_path):
    app = configured_app(tmp_path)
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/hermes-g2/health").json() == {"status": "ok"}
        assert client.get("/v1/snapshot").status_code == 401
        device, headers = pair(client, app)

        action = {
            "kind": "pinSession",
            "deviceId": device["deviceId"],
            "idempotencyKey": "same-action-key",
            "sessionId": "session-1",
            "createdAt": datetime.now(UTC).isoformat(),
        }
        first = client.post("/v1/actions", headers=headers, json=action)
        second = client.post("/v1/actions", headers=headers, json=action)
        assert first.status_code == 200
        assert first.json() == {"status": "ok", "pinned": True}
        assert second.json() == first.json()

        changed = {**action, "payload": {"different": True}}
        assert client.post("/v1/actions", headers=headers, json=changed).status_code == 409


def test_websocket_first_frame_auth_replay_and_ack(tmp_path):
    app = configured_app(tmp_path)
    with TestClient(app) as client:
        device, _ = pair(client, app, "hub")
        event = client.portal.call(app.state.store.append_event, EventInput(
            kind="attention.created",
            source="bridge",
            sessionId="session-1",
            payload={"reason": "input"},
        ))

        with client.websocket_connect("/v1/channel?after=0") as socket:
            socket.send_json({
                "type": "authenticate",
                "deviceId": device["deviceId"],
                "credential": device["credential"],
            })
            replay = socket.receive_json()
            assert replay["eventId"] == event["eventId"]
            assert replay["sessionId"] == "session-1"
            socket.send_json({"type": "ack", "cursor": replay["cursor"]})

            async def await_acknowledgement():
                for _ in range(20):
                    async with app.state.store.connect() as database:
                        row = await (await database.execute(
                            "SELECT acknowledged_cursor FROM devices WHERE id=?",
                            (device["deviceId"],),
                        )).fetchone()
                    if row["acknowledged_cursor"] == event["cursor"]:
                        return row["acknowledged_cursor"]
                    await asyncio.sleep(0.01)
                return 0

            assert client.portal.call(await_acknowledgement) == event["cursor"]


def test_snapshot_exposes_active_runs_with_versioned_camel_case_fields(tmp_path):
    app = configured_app(tmp_path)
    app.state.control.sessions = AsyncMock(return_value=[])
    with TestClient(app) as client:
        _, headers = pair(client, app)
        client.portal.call(
            app.state.store.update_run,
            "run-1",
            "session-1",
            "device-1",
            "started",
            True,
        )

        response = client.get("/v1/snapshot", headers=headers)

        assert response.status_code == 200
        assert response.json()["activeRuns"] == [{
            "runId": "run-1",
            "sessionId": "session-1",
            "deviceId": "device-1",
            "initiatedByG2": True,
            "status": "started",
            "updatedAt": response.json()["activeRuns"][0]["updatedAt"],
        }]
