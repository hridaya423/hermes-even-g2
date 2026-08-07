import httpx

from hermes_g2_bridge.hermes import HermesClient


async def test_capabilities_follow_official_nested_features():
    def handler(request: httpx.Request):
        if request.url.path == "/v1/capabilities":
            return httpx.Response(200, json={"features": {"session_resources": True, "session_chat_streaming": True, "session_run_control": False, "skills_api": True}})
        return httpx.Response(200, json={"ok": True})
    value = HermesClient("http://hermes.test", "secret")
    await value.client.aclose()
    value.client = httpx.AsyncClient(base_url="http://hermes.test", transport=httpx.MockTransport(handler))
    capabilities = await value.probe()
    assert capabilities["nativeSessions"] is True
    assert capabilities["sessionStreaming"] is True
    assert capabilities["sessionRunControl"] is False
    assert capabilities["skills"] is True
    await value.close()


async def test_session_list_is_normalized():
    value = HermesClient("http://hermes.test", "secret")
    await value.client.aclose()
    value.client = httpx.AsyncClient(base_url="http://hermes.test", transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"sessions": [{"id": "s"}]})))
    sessions = await value.list_sessions()
    assert sessions[0]["id"] == "s"
    assert sessions[0]["executionReady"] is True
    await value.close()


async def test_session_list_accepts_live_020_data_envelope():
    value = HermesClient("http://hermes.test", "secret")
    await value.client.aclose()
    value.client = httpx.AsyncClient(
        base_url="http://hermes.test",
        transport=httpx.MockTransport(lambda request: httpx.Response(
            200,
            json={"object": "list", "data": [{"id": "live-session"}], "has_more": False},
        )),
    )
    sessions = await value.list_sessions()
    assert [session["id"] for session in sessions] == ["live-session"]
    await value.close()


async def test_messages_normalize_live_envelope_and_paginate_newest_first():
    rows = [
        {"id": "1", "session_id": "s", "role": "user", "content": "question"},
        {"id": "2", "session_id": "s", "role": "assistant", "content": "answer"},
        {"id": "3", "session_id": "s", "role": "tool", "content": "result", "tool_name": "terminal"},
    ]
    value = HermesClient("http://hermes.test", "secret")
    await value.client.aclose()
    value.client = httpx.AsyncClient(
        base_url="http://hermes.test",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"data": rows})),
    )
    page = await value.messages("s", limit=2, offset=0)
    assert [message["id"] for message in page["data"]] == ["3", "2"]
    assert page["hasMore"] is True
    assert page["total"] == 3
    assert page["data"][0]["toolName"] == "terminal"
    await value.close()


async def test_session_stream_preserves_sse_event_names():
    body = (
        'event: run.started\ndata: {"run_id":"run-1"}\n\n'
        'event: assistant.completed\ndata: {"content":"ready"}\n\n'
        'event: done\ndata: {}\n\n'
    )
    value = HermesClient("http://hermes.test", "secret")
    await value.client.aclose()
    value.client = httpx.AsyncClient(
        base_url="http://hermes.test",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=body)),
    )
    events = [event async for event in value.stream_prompt("session", "hello")]
    assert [event["type"] for event in events] == [
        "run.started", "assistant.completed", "done",
    ]
    await value.close()


async def test_create_session_unwraps_live_020_response():
    value = HermesClient("http://hermes.test", "secret")
    await value.client.aclose()
    value.client = httpx.AsyncClient(
        base_url="http://hermes.test",
        transport=httpx.MockTransport(lambda request: httpx.Response(
            201,
            json={"object": "hermes.session", "session": {"id": "created", "source": "api_server"}},
        )),
    )
    created = await value.create_session({"title": "G2"})
    assert created["id"] == "created"
    assert created["source"] == "api_server"
    await value.close()


async def test_rename_session_targets_exact_native_session():
    captured = {}

    def handler(request: httpx.Request):
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = request.content
        return httpx.Response(
            200,
            json={"session": {"id": "session-1", "title": "Renamed"}},
        )

    value = HermesClient("http://hermes.test", "secret")
    await value.client.aclose()
    value.client = httpx.AsyncClient(
        base_url="http://hermes.test", transport=httpx.MockTransport(handler)
    )
    renamed = await value.rename_session("session-1", "Renamed")
    assert captured == {
        "method": "PATCH",
        "path": "/api/sessions/session-1",
        "body": b'{"title":"Renamed"}',
    }
    assert renamed["id"] == "session-1"
    assert renamed["title"] == "Renamed"
    await value.close()
