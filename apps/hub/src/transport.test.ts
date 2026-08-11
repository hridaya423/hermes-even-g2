import {describe, expect, it, vi} from "vitest";
import type {AgentAction, DurableEvent} from "@hermes-g2/protocol";
import {loadHubPersistence, saveHubPersistence, type StorageLike} from "./state";
import {
  BridgeHttpError,
  HubTransport,
  StaleActionError,
  TransportNotReadyError,
  UncertainActionError,
  backoffDelay,
  type HubApiAdapter,
  type WebSocketLike,
} from "./transport";

function memoryStorage(): StorageLike {
  const values = new Map<string, string>();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => { values.set(key, value); },
    removeItem: (key) => { values.delete(key); },
  };
}

class FakeSocket implements WebSocketLike {
  onopen: (() => void) | undefined;
  onclose: (() => void) | undefined;
  onerror: (() => void) | undefined;
  onmessage: ((event: {data: unknown}) => void) | undefined;
  readonly sent: unknown[] = [];
  closed = false;

  send(value: string): void { this.sent.push(JSON.parse(value)); }
  close(): void { this.closed = true; this.onclose?.(); }
  open(): void { this.onopen?.(); }
  message(value: unknown): void { this.onmessage?.({data: typeof value === "string" ? value : JSON.stringify(value)}); }
}

const action: AgentAction = {
  kind: "prompt",
  deviceId: "device",
  idempotencyKey: "action-12345678",
  sessionId: "session",
  createdAt: new Date().toISOString(),
  payload: {text: "continue"},
};

function event(cursor: number): DurableEvent {
  return {
    protocolVersion: "1.0",
    eventId: `event-${cursor}`,
    cursor,
    kind: "attention.created",
    timestamp: new Date(0).toISOString(),
    source: "bridge",
    sessionId: "session",
    payload: {cursor},
  };
}

function snapshot(cursor: number) {
  return {
    protocolVersion: "1.0" as const,
    sessions: [],
    cursor,
    runtime: {bridge: true, hermes: true, coreReady: true, guiReady: true, tailscale: true, stt: true},
    hermes: {
      nativeSessions: true,
      sessionHistory: true,
      sessionStreaming: true,
      sessionRunControl: true,
      sessionApprovalResponse: true,
      jobs: false,
      models: false,
      skills: false,
      subagents: false,
      attachments: false,
      raw: {},
    },
  };
}

describe("durable hub state", () => {
  it("persists cursor, selection, reading position, transcript destination, and idempotency keys", () => {
    const storage = memoryStorage();
    saveHubPersistence({
      cursor: 41,
      selectedSessionId: "session",
      readingPositions: {session: 3},
      transcriptDestination: "session",
      recordingSessionId: "session",
      idempotencyKeys: ["action-12345678"],
    }, storage);

    expect(loadHubPersistence(storage)).toEqual({
      cursor: 41,
      selectedSessionId: "session",
      readingPositions: {session: 3},
      transcriptDestination: "session",
      recordingSessionId: "session",
      idempotencyKeys: ["action-12345678"],
    });
  });
});

describe("Hub transport recovery", () => {
  it("rejects actions until a fresh snapshot has reconciled", async () => {
    let resolveSnapshot: ((value: ReturnType<typeof snapshot>) => void) | undefined;
    const api: HubApiAdapter = {
      snapshot: () => new Promise((resolve) => { resolveSnapshot = resolve; }),
      action: vi.fn().mockResolvedValue({status: "started"}),
      websocketUrl: () => "wss://bridge.test/v1/channel?after=7",
    };
    const transport = new HubTransport(api, {storage: memoryStorage(), webSocketFactory: () => new FakeSocket()});
    const starting = transport.start();

    await expect(transport.dispatch(action)).rejects.toBeInstanceOf(TransportNotReadyError);
    resolveSnapshot?.(snapshot(7));
    await starting;
    expect(transport.status.reconciled).toBe(true);
  });

  it("keeps the reconnect path snapshot-first after an initial snapshot failure", async () => {
    const socket = new FakeSocket();
    let calls = 0;
    const api: HubApiAdapter = {
      snapshot: vi.fn().mockImplementation(async () => {
        calls += 1;
        if (calls === 1) throw new Error("bridge unavailable");
        return snapshot(4);
      }),
      action: vi.fn().mockResolvedValue({status: "ok"}),
      websocketUrl: () => "wss://bridge.test/v1/channel?after=4",
    };
    const transport = new HubTransport(api, {storage: memoryStorage(), webSocketFactory: () => socket, reconnect: {baseDelayMs: 0, maxDelayMs: 0, jitter: 0}});
    await expect(transport.start()).rejects.toThrow("bridge unavailable");
    await vi.waitFor(() => expect(api.snapshot).toHaveBeenCalledTimes(2));
    expect(transport.status.reconciled).toBe(true);
    transport.stop();
  });

  it("authenticates the WSS channel, acknowledges ordered events, and persists the cursor", async () => {
    const socket = new FakeSocket();
    const received: DurableEvent[] = [];
    const api: HubApiAdapter = {
      snapshot: vi.fn().mockResolvedValue(snapshot(7)),
      action: vi.fn().mockResolvedValue({status: "started"}),
      websocketUrl: () => "wss://bridge.test/v1/channel?after=7",
      credentials: {deviceId: "device", credential: "credential"},
    };
    const storage = memoryStorage();
    const transport = new HubTransport(api, {
      storage,
      webSocketFactory: () => socket,
      onEvent: (value) => received.push(value),
    });
    await transport.start();
    socket.open();
    socket.message(event(8));
    await vi.waitFor(() => expect(socket.sent).toHaveLength(2));

    expect(socket.sent).toEqual([
      {type: "authenticate", deviceId: "device", credential: "credential"},
      {type: "ack", cursor: 8},
    ]);
    expect(received.map((value) => value.cursor)).toEqual([8]);
    expect(loadHubPersistence(storage).cursor).toBe(8);
  });

  it("resnapshots when a replay starts after a compacted cursor", async () => {
    const first = new FakeSocket();
    const second = new FakeSocket();
    let sockets = 0;
    const snapshots = [snapshot(7), snapshot(20)];
    const api: HubApiAdapter = {
      snapshot: vi.fn().mockImplementation(async () => snapshots.shift()),
      action: vi.fn().mockResolvedValue({status: "started"}),
      websocketUrl: (cursor) => `wss://bridge.test/v1/channel?after=${cursor}`,
      credentials: {deviceId: "device", credential: "credential"},
      replay: vi.fn().mockResolvedValue({events: [], nextCursor: 9, hasMore: false, requiresSnapshot: true}),
    };
    const transport = new HubTransport(api, {
      storage: memoryStorage(),
      webSocketFactory: () => (sockets++ === 0 ? first : second),
      reconnect: {baseDelayMs: 0, maxDelayMs: 0, jitter: 0},
    });
    await transport.start();
    first.open();
    first.message(event(8));
    first.close();
    await vi.waitFor(() => expect(api.snapshot).toHaveBeenCalledTimes(2));

    expect(transport.status.phase).toBe("ready");
    expect(transport.status.cursor).toBe(20);
  });

  it("retains an action while disconnected and flushes the same idempotency key after WSS opens", async () => {
    const socket = new FakeSocket();
    const api: HubApiAdapter = {
      snapshot: vi.fn().mockResolvedValue(snapshot(7)),
      action: vi.fn().mockResolvedValue({status: "started"}),
      websocketUrl: () => "wss://bridge.test/v1/channel?after=7",
      credentials: {deviceId: "device", credential: "credential"},
    };
    const transport = new HubTransport(api, {storage: memoryStorage(), webSocketFactory: () => socket});
    await transport.start();
    await expect(transport.dispatch(action)).rejects.toThrow("connection is unavailable");
    expect(transport.status.outbox[0]?.status).toBe("queued");
    socket.open();
    await vi.waitFor(() => expect(api.action).toHaveBeenCalledTimes(1));
    expect(transport.status.outbox[0]?.status).toBe("completed");
    expect((api.action as ReturnType<typeof vi.fn>).mock.calls[0]?.[0].idempotencyKey).toBe(action.idempotencyKey);
  });

  it("surfaces stale actions and uncertain network outcomes for deliberate retry", async () => {
    const socket = new FakeSocket();
    const api: HubApiAdapter = {
      snapshot: vi.fn().mockResolvedValue(snapshot(7)),
      action: vi.fn().mockRejectedValueOnce(new BridgeHttpError(409, "approval is stale, resolved, or does not match"))
        .mockRejectedValueOnce(new Error("network dropped"))
        .mockResolvedValue({status: "ok"}),
      websocketUrl: () => "wss://bridge.test/v1/channel?after=7",
      credentials: {deviceId: "device", credential: "credential"},
    };
    const transport = new HubTransport(api, {storage: memoryStorage(), webSocketFactory: () => socket});
    await transport.start();
    socket.open();
    await expect(transport.dispatch({...action, idempotencyKey: "stale-12345678"})).rejects.toBeInstanceOf(StaleActionError);
    await expect(transport.dispatch({...action, idempotencyKey: "uncertain-12345678"})).rejects.toBeInstanceOf(UncertainActionError);
    await expect(transport.retryAction("uncertain-12345678")).resolves.toEqual({status: "ok"});
    expect(transport.status.outbox.find((item) => item.idempotencyKey === "stale-12345678")?.status).toBe("stale");
  });

  it("uses bounded exponential reconnect jitter", () => {
    expect(backoffDelay(0, {baseDelayMs: 100, maxDelayMs: 1_000, jitter: 0}, () => 0.5)).toBe(100);
    expect(backoffDelay(4, {baseDelayMs: 100, maxDelayMs: 1_000, jitter: 0}, () => 0.5)).toBe(1_000);
  });
});
