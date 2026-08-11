import type {ActiveRun, AgentAction, AgentMessage, ApprovalRequest, DurableEvent, HermesCapabilities, RuntimeReadiness, SessionSummary} from "@hermes-g2/protocol";
import {BridgeHttpError, type HubApiAdapter, type ReplayPage} from "./transport";

export type Credentials = {origin: string; deviceId: string; credential: string};
export type Snapshot = {protocolVersion?: "1.0"; sessions: SessionSummary[] | {items?: SessionSummary[]}; cursor: number; runtime: RuntimeReadiness; hermes: HermesCapabilities; activeRuns?: ActiveRun[]; pendingApprovals?: ApprovalRequest[]};
export type RevokeResult = {supported: boolean; revoked: boolean};

export class BridgeApi implements HubApiAdapter {
  private readonly acknowledgementCreatedAt = new Map<number, string>();

  constructor(public readonly credentials: Credentials) {}

  private headers(extra?: HeadersInit): HeadersInit {
    return {Authorization: `Bearer ${this.credentials.credential}`, "X-Device-Id": this.credentials.deviceId, ...extra};
  }

  private endpoint(path: string): string { return `${this.credentials.origin.replace(/\/$/, "")}${path}`; }

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(this.endpoint(path), {...init, headers: this.headers(init?.headers)});
    if (!response.ok) {
      const body = await response.text();
      let parsed: unknown = body;
      try { parsed = body ? JSON.parse(body) : undefined; } catch { /* retain text */ }
      const detail = typeof parsed === "object" && parsed !== null && "detail" in parsed ? String((parsed as {detail?: unknown}).detail ?? body) : body;
      throw new BridgeHttpError(response.status, detail || `Bridge ${response.status}`, parsed);
    }
    if (response.status === 204) return undefined as T;
    return response.json() as Promise<T>;
  }

  async get<T>(path: string): Promise<T> { return this.request<T>(path); }
  async snapshot(): Promise<Snapshot> { return this.get("/v1/snapshot"); }
  async messages(sessionId: string, limit = 100, offset = 0): Promise<{data: AgentMessage[]; total: number; hasMore: boolean}> { return this.get(`/v1/sessions/${encodeURIComponent(sessionId)}/messages?limit=${limit}&offset=${offset}`); }
  async action(action: AgentAction): Promise<unknown> { return this.request("/v1/actions", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(action)}); }
  async audio(sessionId: string, pcm: Uint8Array): Promise<{transcript: string; duration: number; confidence?: number; sessionId: string}> { return this.request(`/v1/audio?sessionId=${encodeURIComponent(sessionId)}`, {method: "POST", headers: {"Content-Type": "application/octet-stream"}, body: pcm as BodyInit}); }

  websocketUrl(after: number): string {
    const origin = this.credentials.origin.replace(/^https?/, (scheme) => scheme === "https" ? "wss" : "ws").replace(/\/$/, "");
    const url = new URL(`${origin}/v1/channel`);
    url.searchParams.set("after", String(Math.max(0, Math.floor(after))));
    return url.toString();
  }

  async replay(after: number, limit = 500): Promise<ReplayPage> {
    const response = await this.get<Partial<ReplayPage>>(`/v1/events/replay?after=${Math.max(0, Math.floor(after))}&limit=${Math.max(1, Math.min(500, Math.floor(limit)))}`);
    return {
      events: Array.isArray(response.events) ? response.events : [],
      nextCursor: typeof response.nextCursor === "number" ? response.nextCursor : after,
      hasMore: response.hasMore === true,
      gap: response.gap === true,
      resnapshotRequired: response.resnapshotRequired === true,
      requiresSnapshot: response.requiresSnapshot === true,
      oldestCursor: response.oldestCursor,
      latestCursor: response.latestCursor,
    };
  }

  async acknowledge(cursor: number): Promise<void> {
    const value = Math.max(0, Math.floor(cursor));
    const idempotencyKey = `hub-ack-${this.credentials.deviceId}-${value}`;
    const createdAt = this.acknowledgementCreatedAt.get(value) ?? new Date().toISOString();
    this.acknowledgementCreatedAt.set(value, createdAt);
    await this.action({kind: "acknowledge", deviceId: this.credentials.deviceId, idempotencyKey, createdAt, payload: {cursor: value}});
  }

  async revokeDevice(deviceId = this.credentials.deviceId): Promise<RevokeResult> {
    try {
      await this.request(`/v1/devices/${encodeURIComponent(deviceId)}/revoke`, {method: "POST"});
      return {supported: true, revoked: true};
    } catch (error) {
      if (error instanceof BridgeHttpError && error.status === 401) return {supported: true, revoked: true};
      if (error instanceof BridgeHttpError && (error.status === 404 || error.status === 405 || error.status === 501)) return {supported: false, revoked: false};
      throw error;
    }
  }

  channel(after: number, onEvent: (event: DurableEvent) => void, onConnection: (connected: boolean) => void): () => void {
    const socket = new WebSocket(this.websocketUrl(after));
    socket.onopen = () => {
      socket.send(JSON.stringify({type: "authenticate", deviceId: this.credentials.deviceId, credential: this.credentials.credential}));
      onConnection(true);
    };
    socket.onclose = () => onConnection(false);
    socket.onerror = () => onConnection(false);
    socket.onmessage = (message) => {
      let value: unknown;
      try { value = JSON.parse(String(message.data)); } catch { return; }
      if (!value || typeof value !== "object") return;
      const event = value as Partial<DurableEvent>;
      if (event.eventId && typeof event.cursor === "number") {
        onEvent(event as DurableEvent);
        socket.send(JSON.stringify({type: "ack", cursor: event.cursor}));
      }
    };
    return () => socket.close();
  }

  static async exchange(origin: string, code: string): Promise<Credentials> {
    const normalized = origin.replace(/\/$/, "");
    const response = await fetch(`${normalized}/v1/pairings/exchange`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({code, deviceName: "Even G2 Hub", deviceKind: "hub"}),
    });
    if (!response.ok) {
      const body = await response.text();
      throw new BridgeHttpError(response.status, body || `Bridge ${response.status}`, body);
    }
    const paired = await response.json() as {deviceId: string; credential: string};
    return {origin: normalized, deviceId: paired.deviceId, credential: paired.credential};
  }
}

export function loadCredentials(): Credentials | undefined {
  if (typeof localStorage === "undefined") return undefined;
  const raw = localStorage.getItem("hermes-g2.credentials");
  if (!raw) return;
  try {
    const value: unknown = JSON.parse(raw);
    if (typeof value !== "object" || value === null) return;
    const credentials = value as Partial<Credentials>;
    if (typeof credentials.origin !== "string" || typeof credentials.deviceId !== "string" || typeof credentials.credential !== "string") return;
    return credentials as Credentials;
  } catch { return; }
}

export function saveCredentials(value: Credentials): void {
  if (typeof localStorage !== "undefined") localStorage.setItem("hermes-g2.credentials", JSON.stringify(value));
}
