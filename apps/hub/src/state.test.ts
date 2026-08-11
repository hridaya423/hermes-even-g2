import {describe, expect, it} from "vitest";
import {beginRecording, bindTranscript, cycleSession, loadHubPersistence, persistViewState, setReadingPosition, type StorageLike, type ViewState} from "./state";
import * as stateModule from "./state";

const initial: ViewState = {sessions: [{id: "a", title: "A", source: "desktop", executionReady: true, state: "idle", updatedAt: "2026-01-01", pinned: true}, {id: "b", title: "B", source: "telegram", executionReady: true, state: "idle", updatedAt: "2026-01-02", pinned: false}], selected: 0, mode: "default", detailPage: 0, decisionIndex: 0, connected: true, cursor: 0, pending: [], activeRuns: [], latestEvents: {}, history: {}};

describe("immutable prompt routing", () => {
  it("captures the visible session before recording", () => {
    const next = beginRecording(initial);
    expect(next.recordingSessionId).toBe("a");
    expect(next.transcriptDestination).toBe("a");
  });
  it("rejects a transcript returned for another session", () => expect(() => bindTranscript(beginRecording(initial), {text: "hello", duration: 1, sessionId: "b"})).toThrow());
  it("cycles sessions only in navigation", () => expect(cycleSession(initial, -1).sessions[cycleSession(initial, -1).selected].id).toBe("b"));
  it("keeps a detail reading position with its exact session", () => {
    const next = setReadingPosition({...initial, mode: "detail"}, 2);
    expect(next.detailPage).toBe(2);
    expect(next.readingPositions).toEqual({a: 2});
    expect(cycleSession(next, 1).detailPage).toBe(0);
    expect(cycleSession({...next, readingPositions: {a: 2, b: 3}}, 1).detailPage).toBe(3);
  });
  it("clears a persisted transcript destination only when the review is discarded", () => {
    const values = new Map<string, string>();
    const storage: StorageLike = {getItem: (key) => values.get(key) ?? null, setItem: (key, value) => { values.set(key, value); }, removeItem: (key) => { values.delete(key); }};
    persistViewState(beginRecording(initial), storage);
    expect(loadHubPersistence(storage).transcriptDestination).toBe("a");
    persistViewState({...initial, transcriptDestination: undefined, recordingSessionId: undefined}, storage);
    expect(loadHubPersistence(storage).transcriptDestination).toBeUndefined();
  });
});

describe("deliberate run cancellation", () => {
  it("captures the exact active run for the visible session", () => {
    expect(stateModule).toHaveProperty("beginStopConfirmation");
    const state = {
      ...initial,
      activeRuns: [
        {runId: "run-a", sessionId: "a", status: "started"},
        {runId: "run-b", sessionId: "b", status: "started"},
      ],
    } as ViewState;

    const next = (stateModule as any).beginStopConfirmation(state);

    expect(next.mode).toBe("stopConfirmation");
    expect(next.stopTarget).toEqual({sessionId: "a", runId: "run-a"});
  });

  it("cannot enter confirmation without a matching active run", () => {
    expect(stateModule).toHaveProperty("beginStopConfirmation");
    const state = {...initial, activeRuns: []} as ViewState;

    expect((stateModule as any).beginStopConfirmation(state)).toBe(state);
  });

  it("adds started runs and removes only the matching terminal run", () => {
    expect(stateModule).toHaveProperty("applyRunEvent");
    const started = (stateModule as any).applyRunEvent(initial, {
      kind: "run.started",
      sessionId: "a",
      runId: "run-a",
      timestamp: "2026-01-03T00:00:00Z",
      payload: {initiatedByG2: true},
    });
    const withSecondRun = {
      ...started,
      activeRuns: [...started.activeRuns, {runId: "run-b", sessionId: "b", initiatedByG2: false, status: "started", updatedAt: "2026-01-03T00:00:00Z"}],
    };

    const completed = (stateModule as any).applyRunEvent(withSecondRun, {
      kind: "run.completed",
      sessionId: "a",
      runId: "run-a",
      timestamp: "2026-01-03T00:01:00Z",
      payload: {},
    });

    expect(started.activeRuns.map((run: {runId: string}) => run.runId)).toEqual(["run-a"]);
    expect(completed.activeRuns.map((run: {runId: string}) => run.runId)).toEqual(["run-b"]);
  });

  it("renders detail content and advertises deliberate cancellation on glasses", () => {
    expect(stateModule).toHaveProperty("detailContent");
    const state = {
      ...initial,
      mode: "detail",
      history: {a: [{id: "message", sessionId: "a", role: "assistant", content: "Complete answer"}]},
      activeRuns: [{runId: "run-a", sessionId: "a", initiatedByG2: true, status: "started", updatedAt: "now"}],
    } as ViewState;

    const content = (stateModule as any).detailContent(state);

    expect(content).toContain("FULL ANSWER · 1/4");
    expect(content).toContain("Complete answer");
    expect(content).toContain("PRESS TO STOP run-a");
  });
});
