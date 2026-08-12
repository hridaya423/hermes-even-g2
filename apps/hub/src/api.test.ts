import {afterEach, describe, expect, it, vi} from "vitest";
import {BridgeApi, type Credentials} from "./api";

const credentials: Credentials = {origin: "https://bridge.test/base", deviceId: "device", credential: "secret"};

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("BridgeApi transport adapter", () => {
  it("builds an authenticated WSS URL without putting credentials in the URL", () => {
    const api = new BridgeApi(credentials);
    const url = api.websocketUrl(17);
    expect(url).toBe("wss://bridge.test/base/v1/channel?after=17");
    expect(url).not.toContain("secret");
  });

  it("reports an absent replay endpoint as unsupported for a safe fallback", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("missing", {status: 404})));
    const api = new BridgeApi(credentials);
    await expect(api.replay(4)).rejects.toMatchObject({status: 404});
  });

  it("preserves the bridge compaction marker and acknowledges HTTP replay", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({events: [], nextCursor: 4, hasMore: false, requiresSnapshot: true, oldestCursor: 7, latestCursor: 7}), {status: 200, headers: {"Content-Type": "application/json"}}))
      .mockImplementation(async () => new Response(JSON.stringify({status: "acknowledged"}), {status: 200, headers: {"Content-Type": "application/json"}}));
    vi.stubGlobal("fetch", fetchMock);
    const api = new BridgeApi(credentials);
    await expect(api.replay(4)).resolves.toMatchObject({requiresSnapshot: true, oldestCursor: 7, latestCursor: 7});
    await expect(api.acknowledge(7)).resolves.toBeUndefined();
    await expect(api.acknowledge(7)).resolves.toBeUndefined();
    const firstBody = JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body));
    const secondBody = JSON.parse(String(fetchMock.mock.calls[2]?.[1]?.body));
    expect(firstBody).toMatchObject({kind: "acknowledge", payload: {cursor: 7}});
    expect(secondBody).toEqual(firstBody);
  });

  it("reuses the acknowledgement creation time after a Hub reload", async () => {
    const values = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => { values.set(key, value); },
      removeItem: (key: string) => { values.delete(key); },
    });
    const fetchMock = vi.fn().mockImplementation(async () => new Response(JSON.stringify({status: "acknowledged"}), {status: 200, headers: {"Content-Type": "application/json"}}));
    vi.stubGlobal("fetch", fetchMock);

    await new BridgeApi(credentials).acknowledge(21);
    await new BridgeApi(credentials).acknowledge(21);

    const firstBody = JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body));
    const secondBody = JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body));
    expect(secondBody).toEqual(firstBody);
    expect(secondBody.createdAt).toBe(firstBody.createdAt);
  });

  it("only reports revocation complete after the bridge accepts it", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({status: "revoked"}), {status: 200, headers: {"Content-Type": "application/json"}}));
    vi.stubGlobal("fetch", fetchMock);
    const api = new BridgeApi(credentials);
    await expect(api.revokeDevice()).resolves.toEqual({supported: true, revoked: true});
    expect(fetchMock.mock.calls[0]?.[0]).toBe("https://bridge.test/base/v1/devices/device/revoke");
  });

  it("does not pretend an older bridge revoked the credential", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("not found", {status: 404})));
    const api = new BridgeApi(credentials);
    await expect(api.revokeDevice()).resolves.toEqual({supported: false, revoked: false});
  });

  it("treats an already-invalid credential as safely revoked", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("invalid", {status: 401})));
    const api = new BridgeApi(credentials);
    await expect(api.revokeDevice()).resolves.toEqual({supported: true, revoked: true});
  });
});
