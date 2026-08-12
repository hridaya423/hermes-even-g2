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
        {"id": "2", "session_id": "s", "role": "assistant", "content": "answer", "timestamp": 1700000000.5},
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
    assert page["data"][1]["timestamp"] == "2023-11-14T22:13:20.500000Z"
    await value.close()


async def test_messages_sends_upstream_hints_and_bounds_normalized_large_history():
    captured = {}

    def handler(request: httpx.Request):
        captured["params"] = dict(request.url.params)
        rows = [
            {
                "id": str(index),
                "session_id": "s",
                "role": "assistant",
                "content": "x" * (50_000 if index == 9992 else 1),
            }
            for index in range(10_000)
        ]
        return httpx.Response(200, json={"data": rows})

    value = HermesClient("http://hermes.test", "secret")
    await value.client.aclose()
    value.client = httpx.AsyncClient(
        base_url="http://hermes.test", transport=httpx.MockTransport(handler)
    )

    page = await value.messages("s", limit=3, offset=7)

    # Hermes 0.20 ignores the hints and returns the complete oldest-first list;
    # the bridge still exposes the newest-first page without normalising all
    # 10,000 messages or retaining unbounded message text.
    assert captured["params"] == {"limit": "3", "offset": "7"}
    assert [message["id"] for message in page["data"]] == ["9992", "9991", "9990"]
    assert page["total"] == 10_000
    assert page["hasMore"] is True
    assert len(page["data"][0]["content"]) == 12_000
    await value.close()


async def test_messages_accepts_explicit_upstream_page_metadata_and_order():
    value = HermesClient("http://hermes.test", "secret")
    await value.client.aclose()
    value.client = httpx.AsyncClient(
        base_url="http://hermes.test",
        transport=httpx.MockTransport(lambda request: httpx.Response(
            200,
            json={
                "data": [
                    {"id": "newest", "session_id": "s", "role": "assistant", "content": "done"},
                    {"id": "older", "session_id": "s", "role": "user", "content": "go"},
                ],
                "offset": 0,
                "limit": 2,
                "total": 20,
                "has_more": True,
                "order": "desc",
            },
        )),
    )

    page = await value.messages("s", limit=2, offset=0)

    assert [message["id"] for message in page["data"]] == ["newest", "older"]
    assert page["total"] == 20
    assert page["hasMore"] is True
    await value.close()


async def test_fork_session_targets_exact_native_session_and_preserves_payload():
    captured = {}

    def handler(request: httpx.Request):
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = request.content
        return httpx.Response(
            201,
            json={"object": "hermes.session", "session": {"id": "forked", "parent_session_id": "source"}},
        )

    value = HermesClient("http://hermes.test", "secret")
    await value.client.aclose()
    value.client = httpx.AsyncClient(
        base_url="http://hermes.test", transport=httpx.MockTransport(handler)
    )

    forked = await value.fork_session("source", {"title": "alternate path"})

    assert captured == {
        "method": "POST",
        "path": "/api/sessions/source/fork",
        "body": b'{"title":"alternate path"}',
    }
    assert forked["id"] == "forked"
    assert forked["parentSessionId"] == "source"
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


async def test_session_model_lock_targets_exact_native_session():
    captured = {}

    def handler(request: httpx.Request):
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = request.content
        return httpx.Response(200, json={"object": "hermes.session.model_lock"})

    value = HermesClient("http://hermes.test", "secret")
    await value.client.aclose()
    value.client = httpx.AsyncClient(
        base_url="http://hermes.test", transport=httpx.MockTransport(handler)
    )
    await value.set_session_model("session-1", "provider-1", "model-1")
    assert captured == {
        "method": "POST",
        "path": "/api/sessions/session-1/model",
        "body": b'{"provider":"provider-1","model":"model-1"}',
    }
    await value.close()
