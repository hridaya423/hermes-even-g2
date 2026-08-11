import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from hermes_g2_bridge.app import create_app
from hermes_g2_bridge.config import Settings
from hermes_g2_bridge.hermes import HermesError
from hermes_g2_bridge.models import EventInput


def configured_app(tmp_path, **overrides):
    app = create_app(Settings(
        hermes_api_key="test-only-master-key",
        database_path=tmp_path / "bridge.db",
        attachments_root=tmp_path / "attachments",
        whisper_binary=tmp_path / "whisper-cli",
        whisper_model=tmp_path / "model.bin",
        **overrides,
    ))
    app.state.control.probe = AsyncMock(return_value={
        "runtime": {"bridge": True, "hermes": True, "coreReady": True, "guiReady": False},
        "hermes": {"nativeSessions": True, "sessionHistory": True, "sessionStreaming": True},
    })
    app.state.control.reconcile_once = AsyncMock(return_value=0)
    app.state.control.sessions = AsyncMock(return_value=[{
        "id": "session-1", "title": "Test", "state": "idle", "updatedAt": "",
    }])
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


def test_attachment_upload_is_private_session_bound_and_content_addressed(tmp_path):
    app = configured_app(tmp_path)
    with TestClient(app) as client:
        _, headers = pair(client, app, "android")

        assert client.post(
            "/v1/attachments?sessionId=session-1",
            files={"file": ("photo.png", b"not-really-a-png", "image/png")},
        ).status_code == 401

        response = client.post(
            "/v1/attachments?sessionId=session-1",
            headers=headers,
            files={"file": ("photo.png", b"not-really-a-png", "image/png")},
        )

        assert response.status_code == 201
        value = response.json()
        assert value["sessionId"] == "session-1"
        assert value["name"] == "photo.png"
        assert value["mediaType"] == "image/png"
        assert value["size"] == len(b"not-really-a-png")
        assert len(value["sha256"]) == 64
        staged = [path for path in (tmp_path / "attachments").rglob("*") if path.is_file()]
        assert len(staged) == 1
        assert staged[0].read_bytes() == b"not-really-a-png"


def test_attachment_upload_rejects_unknown_session_before_writing(tmp_path):
    app = configured_app(tmp_path)
    with TestClient(app) as client:
        _, headers = pair(client, app, "android")
        response = client.post(
            "/v1/attachments?sessionId=missing-session",
            headers=headers,
            files={"file": ("private.txt", b"must-not-be-written", "text/plain")},
        )
        assert response.status_code == 404
        assert not [path for path in (tmp_path / "attachments").rglob("*") if path.is_file()]


def test_attachment_upload_enforces_device_quota(tmp_path):
    app = configured_app(tmp_path, attachment_device_quota_bytes=12)
    with TestClient(app) as client:
        _, headers = pair(client, app, "android")
        first = client.post(
            "/v1/attachments?sessionId=session-1",
            headers=headers,
            files={"file": ("first.txt", b"12345678", "text/plain")},
        )
        second = client.post(
            "/v1/attachments?sessionId=session-1",
            headers=headers,
            files={"file": ("second.txt", b"abcdefgh", "text/plain")},
        )
        assert first.status_code == 201
        assert second.status_code == 413
        assert len([path for path in (tmp_path / "attachments").rglob("*") if path.is_file()]) == 1


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


def test_event_replay_is_authenticated_bounded_and_cursor_ordered(tmp_path):
    app = configured_app(tmp_path)
    with TestClient(app) as client:
        _, headers = pair(client, app)
        for index in range(3):
            client.portal.call(app.state.store.append_event, EventInput(
                kind="run.progress",
                source="bridge",
                sessionId="session-1",
                payload={"index": index},
            ))

        assert client.get("/v1/events/replay?after=0&limit=2").status_code == 401
        response = client.get("/v1/events/replay?after=0&limit=2", headers=headers)

        assert response.status_code == 200
        assert [event["cursor"] for event in response.json()["events"]] == [1, 2]
        assert response.json()["hasMore"] is True
        assert response.json()["nextCursor"] == 2


def test_event_replay_reports_compaction_gap(tmp_path):
    app = configured_app(tmp_path)
    with TestClient(app) as client:
        _, headers = pair(client, app)
        for index in range(4):
            client.portal.call(app.state.store.append_event, EventInput(
                kind="run.progress", source="bridge", sessionId="session-1", payload={"index": index},
            ))

        async def compact_prefix():
            async with app.state.store.connect() as database:
                await database.execute("DELETE FROM events WHERE cursor < 4")
                await database.commit()

        client.portal.call(compact_prefix)
        response = client.get("/v1/events/replay?after=1", headers=headers)
        assert response.status_code == 200
        assert response.json()["requiresSnapshot"] is True
        assert response.json()["oldestCursor"] == 4
        assert response.json()["latestCursor"] == 4


def test_hermes_dependency_failures_are_clean_service_unavailable_responses(tmp_path):
    app = configured_app(tmp_path)
    app.state.control.sessions = AsyncMock(
        side_effect=HermesError("Hermes GET /api/sessions is unreachable", 503)
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        _, headers = pair(client, app)

        response = client.get("/v1/snapshot", headers=headers)

        assert response.status_code == 503
        assert response.json() == {
            "detail": "Hermes GET /api/sessions is unreachable",
            "code": "hermes_unavailable",
        }
