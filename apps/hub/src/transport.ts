import type {AgentAction, DurableEvent} from "@hermes-g2/protocol";
import type {Snapshot} from "./api";
import {loadHubPersistence, saveHubPersistence, type HubPersistence, type OutboxStatus, type OutboxView, type StorageLike} from "./state";

export type WebSocketLike = {
  onopen?: ((event?: unknown) => void) | null;
  onclose?: ((event?: unknown) => void) | null;
  onerror?: ((event?: unknown) => void) | null;
  onmessage?: ((event: {data: unknown}) => void) | null;
  send(data: string): void;
  close(): void;
};

export type ReplayPage = {
  events: DurableEvent[];
  nextCursor: number;
  hasMore: boolean;
  gap?: boolean;
  resnapshotRequired?: boolean;
  requiresSnapshot?: boolean;
  oldestCursor?: number;
  latestCursor?: number;
};

export type HubApiAdapter = {
  snapshot(): Promise<Snapshot>;
  action(action: AgentAction): Promise<unknown>;
  websocketUrl(after: number): string;
  credentials?: {deviceId: string; credential: string};
  replay?(after: number, limit?: number): Promise<ReplayPage>;
  acknowledge?(cursor: number): Promise<void>;
};

export type ReconnectOptions = {baseDelayMs: number; maxDelayMs: number; jitter: number};
export type HubTransportStatus = {
  phase: "offline" | "reconciling" | "ready" | "reconnecting" | "gap" | "stopped";
  connected: boolean;
  reconciled: boolean;
  cursor: number;
  reconnectAttempt: number;
  snapshot?: Snapshot;
  outbox: OutboxView[];
  error?: string;
};

export type HubTransportOptions = {
  storage?: StorageLike;
  webSocketFactory?: (url: string) => WebSocketLike;
  reconnect?: Partial<ReconnectOptions>;
  now?: () => number;
  random?: () => number;
  onSnapshot?: (snapshot: Snapshot) => void;
  onEvent?: (event: DurableEvent) => void;
  onStatus?: (status: HubTransportStatus) => void;
};

type StoredOutbox = {
  action: AgentAction;
  status: OutboxStatus;
  attempts: number;
  createdAt: string;
  error?: string;
  nextAttemptAt?: number;
  completedAt?: number;
};

const OUTBOX_KEY = "hermes-g2.outbox";
const MAX_COMPLETED_OUTBOX = 100;
const COMPLETED_OUTBOX_RETENTION_MS = 24 * 60 * 60 * 1_000;
const DEFAULT_RECONNECT: ReconnectOptions = {baseDelayMs: 500, maxDelayMs: 30_000, jitter: 0.2};

function browserStorage(): StorageLike | undefined {
  return typeof localStorage === "undefined" ? undefined : localStorage;
}

function defaultWebSocketFactory(url: string): WebSocketLike {
  return new WebSocket(url) as unknown as WebSocketLike;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export class BridgeHttpError extends Error {
  constructor(public readonly status: number, message: string, public readonly body?: unknown) {
    super(message);
    this.name = "BridgeHttpError";
  }
}

export class TransportNotReadyError extends Error {
  constructor() {
    super("Hub is waiting for a fresh bridge snapshot");
    this.name = "TransportNotReadyError";
  }
}

export class TransportUnavailableError extends Error {
  constructor() {
    super("Hub bridge connection is unavailable; action was retained for retry");
    this.name = "TransportUnavailableError";
  }
}

export class StaleActionError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "StaleActionError";
  }
}

export class UncertainActionError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "UncertainActionError";
  }
}

export function backoffDelay(attempt: number, options: ReconnectOptions, random: () => number = Math.random): number {
  const base = Math.min(options.maxDelayMs, options.baseDelayMs * (2 ** Math.max(0, Math.floor(attempt))));
  if (!options.jitter) return Math.max(0, Math.round(base));
  const sample = Math.max(0, Math.min(1, random()));
  const multiplier = 1 + ((sample * 2) - 1) * options.jitter;
  return Math.max(0, Math.min(options.maxDelayMs, Math.round(base * multiplier)));
}

function classifyFailure(error: unknown): OutboxStatus {
  if (error instanceof BridgeHttpError) {
    if (error.status === 400 || error.status === 409 && /(stale|expired|resolved|does not match|empty)/i.test(error.message)) return "stale";
    if (error.status === 409 && /progress|already running|in progress/i.test(error.message)) return "uncertain";
    if (error.status === 408 || error.status === 425 || error.status === 429 || error.status >= 500) return "retry";
    return "stale";
  }
  return "uncertain";
}

function outboxStorageValue(storage: StorageLike | undefined): StoredOutbox[] {
  if (!storage) return [];
  try {
    const parsed: unknown = JSON.parse(storage.getItem(OUTBOX_KEY) ?? "[]");
    if (!Array.isArray(parsed)) return [];
    return parsed.flatMap((item): StoredOutbox[] => {
      if (typeof item !== "object" || item === null) return [];
      const value = item as Partial<StoredOutbox>;
      if (!value.action || typeof value.action !== "object" || typeof value.action.idempotencyKey !== "string") return [];
      const status = value.status === "sending" ? "uncertain" : value.status;
      if (!["queued", "retry", "uncertain", "stale", "completed"].includes(status ?? "")) return [];
      return [{action: value.action as AgentAction, status: status as OutboxStatus, attempts: Number(value.attempts) || 0, createdAt: typeof value.createdAt === "string" ? value.createdAt : new Date(0).toISOString(), error: typeof value.error === "string" ? value.error : undefined, nextAttemptAt: typeof value.nextAttemptAt === "number" ? value.nextAttemptAt : undefined, completedAt: typeof value.completedAt === "number" && Number.isFinite(value.completedAt) ? value.completedAt : undefined}];
    });
  } catch {
    return [];
  }
}

class ActionOutbox {
  private entries: StoredOutbox[];
  private readonly inFlight = new Map<string, Promise<unknown>>();

  constructor(private readonly api: HubApiAdapter, private readonly storage: StorageLike | undefined, private readonly onChange: () => void, private readonly now: () => number) {
    this.entries = outboxStorageValue(storage);
    this.persist(false);
  }

  views(): OutboxView[] {
    return this.entries.map(({action, status, attempts, error, createdAt}) => ({idempotencyKey: action.idempotencyKey, status, attempts, error, createdAt}));
  }

  nextRetryAt(): number | undefined {
    return this.entries.filter((entry) => entry.status === "retry" && entry.nextAttemptAt !== undefined).map((entry) => entry.nextAttemptAt as number).sort((a, b) => a - b)[0];
  }

  enqueue(action: AgentAction): StoredOutbox {
    const existing = this.entries.find((item) => item.action.idempotencyKey === action.idempotencyKey);
    if (existing) return existing;
    const entry: StoredOutbox = {action, status: "queued", attempts: 0, createdAt: action.createdAt};
    this.entries = [...this.entries, entry];
    this.persist();
    return entry;
  }

  async send(idempotencyKey: string): Promise<unknown> {
    const existing = this.inFlight.get(idempotencyKey);
    if (existing) return existing;
    const operation = this.sendOnce(idempotencyKey);
    this.inFlight.set(idempotencyKey, operation);
    try { return await operation; } finally { this.inFlight.delete(idempotencyKey); }
  }

  private async sendOnce(idempotencyKey: string): Promise<unknown> {
    const entry = this.entries.find((item) => item.action.idempotencyKey === idempotencyKey);
    if (!entry) throw new Error("action is not in the outbox");
    if (entry.status === "completed") return undefined;
    if (entry.status === "stale" || entry.status === "uncertain") throw this.errorFor(entry);
    entry.status = "sending";
    entry.attempts += 1;
    entry.error = undefined;
    this.persist();
    try {
      const result = await this.api.action(entry.action);
      entry.status = "completed";
      entry.completedAt = this.now();
      this.persist();
      return result;
    } catch (error) {
      entry.status = classifyFailure(error);
      entry.error = errorMessage(error);
      entry.nextAttemptAt = entry.status === "retry" ? this.now() + Math.min(30_000, 500 * (2 ** Math.max(0, entry.attempts - 1))) : undefined;
      this.persist();
      throw this.errorFor(entry, error);
    }
  }

  async flush(): Promise<void> {
    const candidates = [...this.entries].filter((entry) => (entry.status === "queued" || entry.status === "retry") && (!entry.nextAttemptAt || entry.nextAttemptAt <= this.now()));
    for (const entry of candidates) {
      try { await this.send(entry.action.idempotencyKey); } catch { /* status is retained for the UI and the next reconnect */ }
    }
  }

  async retry(idempotencyKey: string): Promise<unknown> {
    const entry = this.entries.find((item) => item.action.idempotencyKey === idempotencyKey);
    if (!entry) throw new Error("action is not in the outbox");
    if (entry.status !== "uncertain" && entry.status !== "stale" && entry.status !== "retry") return undefined;
    entry.status = "queued";
    entry.nextAttemptAt = undefined;
    this.persist();
    return this.send(idempotencyKey);
  }

  private errorFor(entry: StoredOutbox, original?: unknown): Error {
    const message = entry.error ?? errorMessage(original ?? entry.status);
    if (entry.status === "stale") return new StaleActionError(message);
    if (entry.status === "uncertain") return new UncertainActionError(message);
    return original instanceof Error ? original : new Error(message);
  }

  private persist(notify = true): void {
    this.pruneCompleted();
    if (this.storage) {
      try { this.storage.setItem(OUTBOX_KEY, JSON.stringify(this.entries)); } catch { /* WebView quota is non-fatal */ }
      const previous = loadHubPersistence(this.storage);
      saveHubPersistence({...previous, idempotencyKeys: [...previous.idempotencyKeys, ...this.entries.map((item) => item.action.idempotencyKey)]}, this.storage);
    }
    if (notify) this.onChange();
  }

  private pruneCompleted(): void {
    const cutoff = this.now() - COMPLETED_OUTBOX_RETENTION_MS;
    const recent = this.entries
      .filter((entry) => entry.status === "completed")
      .filter((entry) => entry.completedAt === undefined || entry.completedAt >= cutoff)
      .slice(-MAX_COMPLETED_OUTBOX);
    const keep = new Set(recent);
    this.entries = this.entries.filter((entry) => entry.status !== "completed" || keep.has(entry));
  }
}

export class HubTransport {
  private readonly storage: StorageLike | undefined;
  private readonly options: Required<Pick<HubTransportOptions, "now" | "random">> & HubTransportOptions;
  private readonly reconnectConfig: ReconnectOptions;
  private readonly outbox: ActionOutbox;
  private socket: WebSocketLike | undefined;
  private socketGeneration = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | undefined;
  private outboxRetryTimer: ReturnType<typeof setTimeout> | undefined;
  private reconnectAttempt = 0;
  private eventQueue: Promise<void> = Promise.resolve();
  private started = false;
  private stopped = false;
  private resnapshotInFlight = false;
  private startPromise: Promise<void> | undefined;
  private lifecycleGeneration = 0;
  private _status: HubTransportStatus;

  constructor(private readonly api: HubApiAdapter, options: HubTransportOptions = {}) {
    this.storage = options.storage ?? browserStorage();
    this.options = {...options, now: options.now ?? Date.now, random: options.random ?? Math.random};
    this.reconnectConfig = {...DEFAULT_RECONNECT, ...options.reconnect};
    const persisted = loadHubPersistence(this.storage);
    this._status = {phase: "offline", connected: false, reconciled: false, cursor: persisted.cursor, reconnectAttempt: 0, outbox: []};
    this.outbox = new ActionOutbox(api, this.storage, () => this.emit(), this.options.now);
    this._status.outbox = this.outbox.views();
  }

  get status(): HubTransportStatus { return this._status; }

  start(): Promise<void> {
    if (this.startPromise) return this.startPromise;
    this.stopped = false;
    this.started = true;
    const generation = ++this.lifecycleGeneration;
    this.transition({phase: "reconciling", connected: false, reconciled: false, error: undefined});
    this.startPromise = this.reconcile(generation).catch((error) => {
      if (this.stopped || generation !== this.lifecycleGeneration) return;
      this.startPromise = undefined;
      this.transition({phase: "offline", connected: false, error: errorMessage(error)});
      this.scheduleReconnect();
      throw error;
    });
    return this.startPromise;
  }

  stop(): void {
    this.lifecycleGeneration += 1;
    this.stopped = true;
    this.started = false;
    this.startPromise = undefined;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = undefined;
    if (this.outboxRetryTimer) clearTimeout(this.outboxRetryTimer);
    this.outboxRetryTimer = undefined;
    this.invalidateSocket();
    this.transition({phase: "stopped", connected: false});
  }

  async dispatch(action: AgentAction): Promise<unknown> {
    if (!this._status.reconciled || this._status.phase !== "ready") throw new TransportNotReadyError();
    const entry = this.outbox.enqueue(action);
    if (!this._status.connected) throw new TransportUnavailableError();
    return this.outbox.send(entry.action.idempotencyKey);
  }

  async retryAction(idempotencyKey: string): Promise<unknown> {
    if (!this._status.reconciled || this._status.phase !== "ready") throw new TransportNotReadyError();
    if (!this._status.connected) throw new TransportUnavailableError();
    return this.outbox.retry(idempotencyKey);
  }

  async refresh(): Promise<void> {
    if (this.stopped) return;
    await this.resnapshot("manual snapshot refresh");
  }

  private async reconcile(generation = this.lifecycleGeneration): Promise<void> {
    const snapshot = await this.api.snapshot();
    if (this.stopped || generation !== this.lifecycleGeneration) return;
    this.applySnapshot(snapshot);
    this.transition({phase: "ready", connected: false, reconciled: true, error: undefined, reconnectAttempt: 0});
    this.openChannel(snapshot.cursor);
  }

  private applySnapshot(snapshot: Snapshot): void {
    this._status.cursor = snapshot.cursor;
    this._status.snapshot = snapshot;
    const previous = loadHubPersistence(this.storage);
    saveHubPersistence({...previous, cursor: snapshot.cursor}, this.storage);
    this.options.onSnapshot?.(snapshot);
  }

  private openChannel(after: number): void {
    if (this.stopped) return;
    this.invalidateSocket();
    const generation = ++this.socketGeneration;
    const url = this.api.websocketUrl(after);
    const socket = this.options.webSocketFactory?.(url) ?? defaultWebSocketFactory(url);
    this.socket = socket;
    socket.onopen = () => {
      if (generation !== this.socketGeneration || this.stopped) return;
      if (this.api.credentials) socket.send(JSON.stringify({type: "authenticate", deviceId: this.api.credentials.deviceId, credential: this.api.credentials.credential}));
      this.reconnectAttempt = 0;
      this.transition({phase: "ready", connected: true, reconciled: true, reconnectAttempt: 0, error: undefined});
      void this.outbox.flush();
    };
    socket.onmessage = (message) => {
      this.eventQueue = this.eventQueue.then(() => this.handleMessage(generation, socket, message.data)).catch((error) => {
        if (!this.stopped) this.transition({error: errorMessage(error)});
      });
    };
    socket.onerror = () => {
      if (generation === this.socketGeneration && !this.stopped) this.transition({connected: false});
    };
    socket.onclose = () => {
      if (generation !== this.socketGeneration || this.stopped) return;
      this.socket = undefined;
      this.transition({phase: "reconnecting", connected: false});
      this.scheduleReconnect();
    };
  }

  private async handleMessage(generation: number, socket: WebSocketLike, raw: unknown): Promise<void> {
    if (generation !== this.socketGeneration || this.stopped) return;
    let message: unknown = raw;
    if (typeof raw === "string") {
      try { message = JSON.parse(raw); } catch { return; }
    }
    if (!message || typeof message !== "object") return;
    const event = message as Partial<DurableEvent> & {type?: string; gap?: boolean; resnapshotRequired?: boolean; requiresSnapshot?: boolean};
    if (event.gap || event.resnapshotRequired || event.requiresSnapshot) return this.resnapshot("bridge replay gap");
    const eventCursor = event.cursor;
    if (!event.eventId || typeof eventCursor !== "number" || !Number.isSafeInteger(eventCursor) || eventCursor < 0) return;
    if (eventCursor <= this._status.cursor) {
      socket.send(JSON.stringify({type: "ack", cursor: this._status.cursor}));
      return;
    }
    if (eventCursor !== this._status.cursor + 1) return this.resnapshot("cursor gap");
    const durable = event as DurableEvent;
    this._status.cursor = eventCursor;
    const previous = loadHubPersistence(this.storage);
    saveHubPersistence({...previous, cursor: durable.cursor}, this.storage);
    this.options.onEvent?.(durable);
    socket.send(JSON.stringify({type: "ack", cursor: durable.cursor}));
    this.emit();
  }

  private scheduleReconnect(): void {
    if (this.stopped || this.reconnectTimer) return;
    const attempt = this.reconnectAttempt++;
    const delay = backoffDelay(attempt, this.reconnectConfig, this.options.random);
    this.transition({phase: "reconnecting", connected: false, reconnectAttempt: this.reconnectAttempt});
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = undefined;
      void this.reconnect();
    }, delay);
  }

  private async reconnect(): Promise<void> {
    if (this.stopped || !this.started) return;
    try {
      if (!this._status.reconciled) {
        await this.reconcile(this.lifecycleGeneration);
        return;
      }
      if (this.api.replay) {
        const replayed = await this.replayFromCursor();
        if (replayed === "gap") {
          await this.resnapshot("replay gap");
          return;
        }
      }
      this.openChannel(this._status.cursor);
    } catch (error) {
      if (this.isUnsupported(error)) {
        this.openChannel(this._status.cursor);
        return;
      }
      this.transition({phase: "reconnecting", connected: false, error: errorMessage(error)});
      this.scheduleReconnect();
    }
  }

  private async replayFromCursor(): Promise<"ok" | "gap"> {
    if (!this.api.replay) return "ok";
    let cursor = this._status.cursor;
    for (;;) {
      const pageStart = cursor;
      const page = await this.api.replay(cursor, 500);
      if (page.gap || page.resnapshotRequired || page.requiresSnapshot) return "gap";
      if (typeof page.oldestCursor === "number" && pageStart < page.oldestCursor - 1) return "gap";
      if (!page.events.length && !page.hasMore && typeof page.latestCursor === "number" && page.latestCursor > pageStart) return "gap";
      for (const event of page.events) {
        if (!Number.isSafeInteger(event.cursor) || event.cursor < 0) return "gap";
        if (event.cursor > cursor + 1) return "gap";
        if (event.cursor <= cursor) continue;
        this._status.cursor = event.cursor;
        cursor = event.cursor;
        const previous = loadHubPersistence(this.storage);
        saveHubPersistence({...previous, cursor}, this.storage);
        this.options.onEvent?.(event);
      }
      if (this.api.acknowledge && cursor > pageStart) {
        await this.api.acknowledge(cursor);
      }
      if (!page.hasMore) return "ok";
      // The bridge returns the last event cursor in this page, so it equals
      // the cursor after applying the page. Comparing against `cursor` here
      // would reject every multi-page replay after the first page.
      if (!Number.isSafeInteger(page.nextCursor) || page.nextCursor <= pageStart || page.nextCursor !== cursor) return "gap";
      cursor = page.nextCursor;
    }
  }

  private async resnapshot(reason: string): Promise<void> {
    if (this.resnapshotInFlight || this.stopped) return;
    this.resnapshotInFlight = true;
    const generation = this.lifecycleGeneration;
    this.transition({phase: "gap", connected: false, error: reason});
    this.invalidateSocket();
    try {
      const snapshot = await this.api.snapshot();
      if (this.stopped || generation !== this.lifecycleGeneration) return;
      this.applySnapshot(snapshot);
      this.reconnectAttempt = 0;
      this.transition({phase: "ready", connected: false, reconciled: true, reconnectAttempt: 0, error: undefined});
      this.openChannel(snapshot.cursor);
      void this.outbox.flush();
    } catch (error) {
      this.transition({phase: "reconnecting", connected: false, error: errorMessage(error)});
      this.scheduleReconnect();
    } finally {
      this.resnapshotInFlight = false;
    }
  }

  private invalidateSocket(): void {
    this.socketGeneration += 1;
    const socket = this.socket;
    this.socket = undefined;
    if (socket) {
      socket.onopen = null;
      socket.onclose = null;
      socket.onerror = null;
      socket.onmessage = null;
      try { socket.close(); } catch { /* a closed browser socket is harmless */ }
    }
  }

  private isUnsupported(error: unknown): boolean {
    return error instanceof BridgeHttpError && (error.status === 404 || error.status === 405 || error.status === 501);
  }

  private transition(change: Partial<HubTransportStatus>): void {
    this._status = {...this._status, ...change, outbox: this.outbox.views()};
    this.options.onStatus?.(this._status);
    this.scheduleOutboxRetry();
  }

  private emit(): void {
    this._status = {...this._status, outbox: this.outbox.views()};
    this.options.onStatus?.(this._status);
    this.scheduleOutboxRetry();
  }

  private scheduleOutboxRetry(): void {
    if (this.stopped || !this._status.connected || this.outboxRetryTimer) return;
    const next = this.outbox.nextRetryAt();
    if (next === undefined) return;
    const delay = Math.max(0, next - this.options.now());
    this.outboxRetryTimer = setTimeout(() => {
      this.outboxRetryTimer = undefined;
      if (this._status.connected) void this.outbox.flush();
    }, delay);
  }
}

export type {HubPersistence};
