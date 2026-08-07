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
