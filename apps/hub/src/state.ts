import type { ActiveRun, AgentMessage, ApprovalRequest, DurableEvent, HermesCapabilities, RuntimeReadiness, SessionSummary } from "@hermes-g2/protocol";

export type Mode = "default" | "detail" | "recording" | "transcript" | "approval" | "confirmation" | "stopConfirmation";
export type Transcript = {text: string; duration: number; confidence?: number | null; sessionId: string};
export type StopTarget = {sessionId: string; runId: string};
export type HubPhase = "offline" | "reconciling" | "ready" | "reconnecting" | "gap" | "stopped";
export type OutboxStatus = "queued" | "sending" | "retry" | "uncertain" | "stale" | "completed";
export type OutboxView = {idempotencyKey: string; status: OutboxStatus; attempts: number; error?: string; createdAt: string};

export type StorageLike = {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
};

export type HubPersistence = {
  cursor: number;
  selectedSessionId?: string;
  readingPositions: Record<string, number>;
  transcriptDestination?: string;
  recordingSessionId?: string;
  idempotencyKeys: string[];
};

export const HUB_PERSISTENCE_KEY = "hermes-g2.state";

const emptyPersistence = (): HubPersistence => ({cursor: 0, readingPositions: {}, idempotencyKeys: []});

function browserStorage(): StorageLike | undefined {
  return typeof localStorage === "undefined" ? undefined : localStorage;
}

function record(value: unknown): Record<string, unknown> | undefined {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
}

function positiveInteger(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? Math.floor(value) : fallback;
}

export function loadHubPersistence(storage: StorageLike | undefined = browserStorage()): HubPersistence {
  const fallback = emptyPersistence();
  if (!storage) return fallback;
  let parsed: Record<string, unknown> | undefined;
  let raw: string | null = null;
  try { raw = storage.getItem(HUB_PERSISTENCE_KEY); } catch { raw = null; }
  try { parsed = record(JSON.parse(raw ?? "")); } catch { parsed = undefined; }
  if (!parsed) {
    let legacyCursorValue: string | null = null;
    let legacySelection: string | null = null;
    try {
      legacyCursorValue = storage.getItem("hermes-g2.cursor");
      legacySelection = storage.getItem("hermes-g2.selected");
    } catch { /* a denied WebView store behaves like an empty store */ }
    const legacyCursor = Number(legacyCursorValue ?? 0);
    return {
      ...fallback,
      cursor: Number.isFinite(legacyCursor) && legacyCursor >= 0 ? Math.floor(legacyCursor) : 0,
      selectedSessionId: legacySelection || undefined,
    };
  }

  const positions = record(parsed.readingPositions);
  const readingPositions: Record<string, number> = {};
  for (const [sessionId, position] of Object.entries(positions ?? {})) {
    if (sessionId && typeof position === "number" && Number.isFinite(position) && position >= 0) readingPositions[sessionId] = Math.floor(position);
  }
  const keys = Array.isArray(parsed.idempotencyKeys) ? parsed.idempotencyKeys.filter((key): key is string => typeof key === "string" && key.length >= 8).slice(-200) : [];
  return {
    cursor: positiveInteger(parsed.cursor, 0),
    selectedSessionId: typeof parsed.selectedSessionId === "string" && parsed.selectedSessionId ? parsed.selectedSessionId : undefined,
    readingPositions,
    transcriptDestination: typeof parsed.transcriptDestination === "string" && parsed.transcriptDestination ? parsed.transcriptDestination : undefined,
    recordingSessionId: typeof parsed.recordingSessionId === "string" && parsed.recordingSessionId ? parsed.recordingSessionId : undefined,
    idempotencyKeys: keys,
  };
}

export function saveHubPersistence(value: HubPersistence, storage: StorageLike | undefined = browserStorage()): void {
  if (!storage) return;
  const normalized: HubPersistence = {
    cursor: positiveInteger(value.cursor, 0),
    selectedSessionId: value.selectedSessionId || undefined,
    readingPositions: Object.fromEntries(Object.entries(value.readingPositions ?? {}).filter(([key, position]) => key && Number.isFinite(position) && position >= 0).map(([key, position]) => [key, Math.floor(position)])),
    transcriptDestination: value.transcriptDestination || undefined,
    recordingSessionId: value.recordingSessionId || undefined,
    idempotencyKeys: [...new Set((value.idempotencyKeys ?? []).filter((key) => key.length >= 8))].slice(-200),
  };
  try {
    storage.setItem(HUB_PERSISTENCE_KEY, JSON.stringify(normalized));
    storage.setItem("hermes-g2.cursor", String(normalized.cursor));
    if (normalized.selectedSessionId) storage.setItem("hermes-g2.selected", normalized.selectedSessionId);
    else storage.removeItem("hermes-g2.selected");
  } catch {
    // A full or unavailable WebView storage must not stop the live connection.
  }
}

export function persistViewState(state: ViewState, storage: StorageLike | undefined = browserStorage(), idempotencyKeys: string[] = []): void {
  const selected = visibleSession(state);
  const previous = loadHubPersistence(storage);
  saveHubPersistence({
    cursor: state.cursor,
    selectedSessionId: selected?.id ?? previous.selectedSessionId,
    readingPositions: state.readingPositions ?? previous.readingPositions,
    transcriptDestination: state.transcriptDestination,
    recordingSessionId: state.recordingSessionId,
    idempotencyKeys: [...previous.idempotencyKeys, ...idempotencyKeys],
  }, storage);
}

export type ViewState = {
  sessions: SessionSummary[]; selected: number; mode: Mode; detailPage: number; decisionIndex: number;
  connected: boolean; cursor: number; pending: ApprovalRequest[]; transcript?: Transcript; recordingSessionId?: string;
  activeRuns: ActiveRun[]; stopTarget?: StopTarget;
  latestEvents: Record<string, DurableEvent[]>; history: Record<string, AgentMessage[]>; notice?: string;
  phase?: HubPhase; reconciled?: boolean; runtime?: RuntimeReadiness; hermes?: HermesCapabilities;
  readingPositions?: Record<string, number>; transcriptDestination?: string; outbox?: OutboxView[];
};

export function visibleSession(state: ViewState): SessionSummary | undefined { return state.sessions[state.selected]; }
export function cycleSession(state: ViewState, delta: number): ViewState {
  if (!state.sessions.length) return state;
  const selected = (state.selected + delta + state.sessions.length) % state.sessions.length;
  return {...state, selected, mode: "default", detailPage: state.readingPositions?.[state.sessions[selected].id] ?? 0};
}
export function beginRecording(state: ViewState): ViewState {
  const session = visibleSession(state);
  return session ? {...state, mode: "recording", recordingSessionId: session.id, transcriptDestination: session.id, transcript: undefined} : state;
}
export function bindTranscript(state: ViewState, transcript: Transcript): ViewState {
  if (transcript.sessionId !== state.recordingSessionId) throw new Error("transcript destination changed while recording");
  return {...state, mode: "transcript", transcript, transcriptDestination: transcript.sessionId};
}

export function setReadingPosition(state: ViewState, position: number): ViewState {
  const session = visibleSession(state);
  if (!session || !Number.isFinite(position)) return state;
  const value = Math.max(0, Math.floor(position));
  return {...state, detailPage: value, readingPositions: {...state.readingPositions, [session.id]: value}};
}
export function beginStopConfirmation(state: ViewState): ViewState {
  const session = visibleSession(state);
  const run = session ? state.activeRuns.find((item) => item.sessionId === session.id) : undefined;
  return run ? {...state, mode: "stopConfirmation", stopTarget: {sessionId: session!.id, runId: run.runId}} : state;
}
export function applyRunEvent(state: ViewState, event: DurableEvent): ViewState {
  if (!event.sessionId || !event.runId) return state;
  if (["run.completed", "run.failed", "run.cancelled"].includes(event.kind)) {
    return {...state, activeRuns: state.activeRuns.filter((run) => run.sessionId !== event.sessionId || run.runId !== event.runId)};
  }
  if (event.kind !== "run.started") return state;
  const payload = typeof event.payload === "object" && event.payload ? event.payload as Record<string, unknown> : {};
  const run: ActiveRun = {runId: event.runId, sessionId: event.sessionId, initiatedByG2: payload.initiatedByG2 === true, status: "started", updatedAt: event.timestamp};
  return {...state, activeRuns: [run, ...state.activeRuns.filter((item) => item.sessionId !== run.sessionId || item.runId !== run.runId)]};
}
export function detailContent(state: ViewState): string {
  const session = visibleSession(state);
  if (!session) return "NO SESSION";
  const events = state.latestEvents[session.id] ?? [];
  const history = state.history[session.id] ?? [];
  const pages = [
    {title: "FULL ANSWER", value: history.find((message) => message.role === "assistant" && message.content.trim())?.content ?? [...events].reverse().find((event) => event.kind === "message.completed")?.payload},
    {title: "TOOLS", value: events.filter((event) => event.kind.startsWith("tool.")).slice(-6).map((event) => event.payload)},
    {title: "SUBAGENTS", value: events.filter((event) => event.kind.startsWith("subagent.")).slice(-6).map((event) => event.payload)},
    {title: "PROVENANCE", value: session},
  ];
  const pageIndex = Math.max(0, Math.min(pages.length - 1, state.detailPage));
  const page = pages[pageIndex];
  const activeRun = state.activeRuns.find((run) => run.sessionId === session.id);
  const value = typeof page.value === "string" ? page.value : JSON.stringify(page.value ?? "No activity yet.", null, 2);
  const instruction = activeRun ? `PRESS TO STOP ${activeRun.runId} · DOUBLE PRESS BACK` : "SWIPE PAGES · DOUBLE PRESS BACK";
  return `${page.title} · ${pageIndex + 1}/${pages.length}\n${value}\n\n${instruction}`;
}
