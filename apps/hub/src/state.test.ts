import {describe, expect, it} from "vitest";
import {beginRecording, bindTranscript, cycleSession, type ViewState} from "./state";

const initial: ViewState = {sessions: [{id: "a", title: "A", source: "desktop", executionReady: true, state: "idle", updatedAt: "2026-01-01", pinned: true}, {id: "b", title: "B", source: "telegram", executionReady: true, state: "idle", updatedAt: "2026-01-02", pinned: false}], selected: 0, mode: "default", detailPage: 0, decisionIndex: 0, connected: true, cursor: 0, pending: [], latestEvents: {}, history: {}};

describe("immutable prompt routing", () => {
  it("captures the visible session before recording", () => expect(beginRecording(initial).recordingSessionId).toBe("a"));
  it("rejects a transcript returned for another session", () => expect(() => bindTranscript(beginRecording(initial), {text: "hello", duration: 1, sessionId: "b"})).toThrow());
  it("cycles sessions only in navigation", () => expect(cycleSession(initial, -1).sessions[cycleSession(initial, -1).selected].id).toBe("b"));
});
