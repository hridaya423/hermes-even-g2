import type { AgentMessage, ApprovalRequest, DurableEvent, SessionSummary } from "@hermes-g2/protocol";

export type Mode = "default" | "detail" | "recording" | "transcript" | "approval" | "confirmation";
export type Transcript = {text: string; duration: number; confidence?: number | null; sessionId: string};
export type ViewState = {
  sessions: SessionSummary[]; selected: number; mode: Mode; detailPage: number; decisionIndex: number;
  connected: boolean; cursor: number; pending: ApprovalRequest[]; transcript?: Transcript; recordingSessionId?: string;
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
