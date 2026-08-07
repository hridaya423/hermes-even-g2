import type { AgentAction, AgentMessage, ApprovalRequest, DurableEvent, HermesCapabilities, RuntimeReadiness, SessionSummary } from "@hermes-g2/protocol";

export type Credentials = {origin: string; deviceId: string; credential: string};
export type Snapshot = {sessions: SessionSummary[] | {items?: SessionSummary[]}; cursor: number; runtime: RuntimeReadiness; hermes: HermesCapabilities; pendingApprovals?: ApprovalRequest[]};

export class BridgeApi {
  constructor(private credentials: Credentials) {}
  private headers(extra?: HeadersInit): HeadersInit { return {Authorization: `Bearer ${this.credentials.credential}`, "X-Device-Id": this.credentials.deviceId, ...extra}; }
  async get<T>(path: string): Promise<T> { const response = await fetch(`${this.credentials.origin}${path}`, {headers: this.headers()}); if (!response.ok) throw new Error(`Bridge ${response.status}`); return response.json(); }
  async snapshot(): Promise<Snapshot> { return this.get("/v1/snapshot"); }
  async messages(sessionId: string, limit = 100, offset = 0): Promise<{data: AgentMessage[]; total: number; hasMore: boolean}> { return this.get(`/v1/sessions/${encodeURIComponent(sessionId)}/messages?limit=${limit}&offset=${offset}`); }
  async action(action: AgentAction): Promise<unknown> { const response = await fetch(`${this.credentials.origin}/v1/actions`, {method: "POST", headers: this.headers({"Content-Type": "application/json"}), body: JSON.stringify(action)}); if (!response.ok) throw new Error(await response.text()); return response.json(); }
  async audio(sessionId: string, pcm: Uint8Array): Promise<{transcript: string; duration: number; confidence?: number; sessionId: string}> { const response = await fetch(`${this.credentials.origin}/v1/audio?sessionId=${encodeURIComponent(sessionId)}`, {method: "POST", headers: this.headers({"Content-Type": "application/octet-stream"}), body: pcm as BodyInit}); if (!response.ok) throw new Error(await response.text()); return response.json(); }
  channel(after: number, onEvent: (event: DurableEvent) => void, onConnection: (connected: boolean) => void): () => void {
    const url = new URL(`${this.credentials.origin.replace(/^http/, "ws")}/v1/channel`); url.searchParams.set("after", String(after));
    const socket = new WebSocket(url); socket.onopen = () => { socket.send(JSON.stringify({type: "authenticate", deviceId: this.credentials.deviceId, credential: this.credentials.credential})); onConnection(true); }; socket.onclose = () => onConnection(false); socket.onerror = () => onConnection(false); socket.onmessage = (message) => { const event = JSON.parse(message.data) as DurableEvent; if (event.eventId) { onEvent(event); socket.send(JSON.stringify({type: "ack", cursor: event.cursor})); } }; return () => socket.close();
  }

  static async exchange(origin: string, code: string): Promise<Credentials> {
    const normalized = origin.replace(/\/$/, "");
    const response = await fetch(`${normalized}/v1/pairings/exchange`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({code, deviceName: "Even G2 Hub", deviceKind: "hub"}),
    });
    if (!response.ok) throw new Error(await response.text());
    const paired = await response.json() as {deviceId: string; credential: string};
    return {origin: normalized, deviceId: paired.deviceId, credential: paired.credential};
  }
}

export function loadCredentials(): Credentials | undefined { const raw = localStorage.getItem("hermes-g2.credentials"); if (!raw) return; try { return JSON.parse(raw); } catch { return; } }
export function saveCredentials(value: Credentials): void { localStorage.setItem("hermes-g2.credentials", JSON.stringify(value)); }
