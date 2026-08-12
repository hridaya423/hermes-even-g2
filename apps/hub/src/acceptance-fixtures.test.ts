import {describe, expect, it} from "vitest";
import type {ApprovalRequest, DurableEvent, SessionSummary} from "@hermes-g2/protocol";
import {approvalDisplay, buildGlassView, detailPages, GLASS_BODY_LINES, GLASS_BODY_WIDTH, paginateText, runtimeDisplay} from "./presentation";
import {beginRecording, bindTranscript, cycleSession, type ViewState} from "./state";

const timestamp = "2026-08-12T10:00:00.000Z";

function session(id: string, overrides: Partial<SessionSummary> = {}): SessionSummary {
  return {
    id,
    title: `Fixture ${id}`,
    source: "desktop",
    model: "fixture-model",
    provider: "hermes",
    workspace: "/Users/hriday/honey/hermes",
    executionReady: true,
    state: "idle",
    updatedAt: timestamp,
    pinned: false,
    ...overrides,
  };
}

function baseState(overrides: Partial<ViewState> = {}): ViewState {
  const active = session("desktop-session", {state: "busy", pinned: true});
  return {
    sessions: [active],
    selected: 0,
    mode: "default",
    detailPage: 0,
    decisionIndex: 0,
    connected: true,
    cursor: 10,
    pending: [],
    activeRuns: [{runId: "run-1", sessionId: active.id, initiatedByG2: true, status: "started", updatedAt: timestamp}],
    latestEvents: {},
    history: {},
    phase: "ready",
    reconciled: true,
    runtime: {bridge: true, hermes: true, coreReady: true, guiReady: false, tailscale: true, stt: true},
    ...overrides,
  };
}

function event(kind: DurableEvent["kind"], sessionId: string, payload: unknown, cursor = 11): DurableEvent {
  return {protocolVersion: "1.0", eventId: `fixture-${kind}`, cursor, kind, timestamp, source: "hermes", sessionId, runId: "run-1", payload};
}

describe("Hermes G2 physical simulator fixture matrix", () => {
  it.each([
    ["offline", {phase: "offline" as const, connected: false}, "OFFLINE"],
    ["reconciling", {phase: "reconciling" as const, reconciled: false}, "SYNCHRONIZING"],
    ["replay gap", {phase: "gap" as const}, "REPLAY GAP"],
    ["reconnecting", {phase: "reconnecting" as const, connected: false}, "RECONNECTING"],
    ["snapshot ready but socket closed", {phase: "ready" as const, connected: false}, "CONNECTING"],
  ])("renders the %s recovery state", (_name, input, expected) => {
    const view = buildGlassView(baseState(input));
    expect(view.body).toContain(expected);
    expect(view.footer).toContain("CORE");
  });

  it("cycles two sessions without changing the captured prompt destination", () => {
    const original = baseState({sessions: [session("desktop-session"), session("telegram-session", {source: "telegram"})]});
    const recording = beginRecording(original);
    const next = cycleSession(recording, 1);
    expect(recording.recordingSessionId).toBe("desktop-session");
    expect(recording.transcriptDestination).toBe("desktop-session");
    expect(next.sessions[next.selected].id).toBe("telegram-session");
    expect(() => bindTranscript(recording, {text: "answer", duration: 1, sessionId: "telegram-session"})).toThrow("destination");
  });

  it("renders every Hermes approval scope and requires explicit confirmation for dangerous work", () => {
    const choices: ApprovalRequest["choices"] = ["once", "session", "always", "deny"];
    for (const [index, choice] of choices.entries()) {
      const request: ApprovalRequest = {requestId: `approval-${choice}`, sessionId: "desktop-session", runId: "run-1", tool: "shell", command: "npm test", destructive: choice !== "once", sensitive: false, choices};
      const display = approvalDisplay(request, index, index === 1 ? "confirmation" : "approval");
      expect(display.choices).toEqual(["ONCE", "SESSION", "ALWAYS", "DENY"]);
      expect(display.selected).toBe(choice.toUpperCase());
      expect(display.body).toContain("npm test");
      if (index === 1) expect(display.body).toContain("PRESS AGAIN TO CONFIRM SESSION");
    }
  });

  it("keeps sensitive approval details on the phone while retaining choices", () => {
    const display = approvalDisplay({requestId: "secret", sessionId: "desktop-session", runId: "run-1", tool: "secrets", command: "cat ~/.env", destructive: true, sensitive: true, choices: ["once", "deny"]}, 0, "approval");
    expect(display.phoneOnly).toBe(true);
    expect(display.body).toContain("PHONE REQUIRED");
    expect(display.body).not.toContain("cat ~/.env");
    expect(display.choices).toEqual(["ONCE", "DENY"]);
  });

  it("shows queued and stopped/failure surfaces without losing run identity", () => {
    const queued = buildGlassView(baseState({sessions: [session("queued", {state: "queued"})], activeRuns: []}));
    expect(queued.body).toContain("QUEUED");
    const failedSession = session("failed", {state: "failed", executionReady: false});
    const failed = buildGlassView(baseState({sessions: [failedSession], activeRuns: [], latestEvents: {[failedSession.id]: [event("run.failed", failedSession.id, {error: "Provider stopped"})]}}));
    expect(failed.body).toContain("FAILED");
    expect(failed.body).toContain("Provider stopped");
  });

  it("renders tool, subagent and job fixture events in detail/progress views", () => {
    const id = "desktop-session";
    const latestEvents = {
      [id]: [
        event("tool.started", id, {tool: "shell", command: "npm test", status: "running"}),
        event("subagent.started", id, {name: "lint", status: "running", summary: "Checking types"}, 12),
        event("subagent.completed", id, {name: "lint", status: "completed", summary: "No findings"}, 13),
        event("job.updated", id, {job: "nightly-sync", status: "completed", summary: "Job finished"}, 14),
      ],
    };
    const state = baseState({latestEvents});
    const view = buildGlassView(state);
    expect(view.body).toContain("SUBAGENT");
    const pages = detailPages(state);
    expect(pages.some((page) => page.title === "TOOLS" && page.text.includes("SHELL"))).toBe(true);
    expect(pages.some((page) => page.title === "SUBAGENTS" && page.text.includes("LINT"))).toBe(true);
    expect(pages.some((page) => page.title === "PROVENANCE" && page.text.includes("EXECUTION READY"))).toBe(true);
  });

  it("reports core and GUI readiness independently", () => {
    expect(runtimeDisplay({bridge: true, hermes: true, coreReady: true, guiReady: false, tailscale: true, stt: true})).toBe("LINK READY · CORE READY · GUI WAIT");
    expect(runtimeDisplay({bridge: true, hermes: true, coreReady: false, guiReady: true, tailscale: true, stt: true})).toBe("LINK READY · CORE WAIT · GUI READY");
  });

  it("keeps long responses and unbroken output within simulator page bounds", () => {
    const long = `${"x".repeat(900)}\n${"Useful Hermes output ".repeat(60)}TAIL_MARKER`;
    const pages = paginateText(long, 180);
    expect(pages.every((page) => page.length <= 180)).toBe(true);
    expect(pages.join("")).toContain("TAIL_MARKER");
    const id = "desktop-session";
    const state = baseState({history: {[id]: [{id: "answer", sessionId: id, role: "assistant", content: long}]}, sessions: [session(id, {state: "idle"})], activeRuns: []});
    const view = buildGlassView({...state, mode: "detail"});
    const lines = view.body.split("\n");
    expect(lines.length).toBeLessThanOrEqual(GLASS_BODY_LINES);
    expect(lines.every((line) => line.length <= GLASS_BODY_WIDTH)).toBe(true);
  });
});
