import type { ActiveRun, AgentMessage, ApprovalRequest, DurableEvent, SessionSummary } from "@hermes-g2/protocol";

export type Mode = "default" | "detail" | "recording" | "transcript" | "approval" | "confirmation" | "stopConfirmation";
export type Transcript = {text: string; duration: number; confidence?: number | null; sessionId: string};
export type StopTarget = {sessionId: string; runId: string};
export type ViewState = {
  sessions: SessionSummary[]; selected: number; mode: Mode; detailPage: number; decisionIndex: number;
  connected: boolean; cursor: number; pending: ApprovalRequest[]; transcript?: Transcript; recordingSessionId?: string;
  activeRuns: ActiveRun[]; stopTarget?: StopTarget;
  latestEvents: Record<string, DurableEvent[]>; history: Record<string, AgentMessage[]>; notice?: string;
};

export function visibleSession(state: ViewState): SessionSummary | undefined { return state.sessions[state.selected]; }
export function cycleSession(state: ViewState, delta: number): ViewState {
  if (!state.sessions.length) return state;
  return {...state, selected: (state.selected + delta + state.sessions.length) % state.sessions.length, mode: "default", detailPage: 0};
}
export function beginRecording(state: ViewState): ViewState {
  const session = visibleSession(state);
  return session ? {...state, mode: "recording", recordingSessionId: session.id, transcript: undefined} : state;
}
export function bindTranscript(state: ViewState, transcript: Transcript): ViewState {
  if (transcript.sessionId !== state.recordingSessionId) throw new Error("transcript destination changed while recording");
  return {...state, mode: "transcript", transcript};
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
