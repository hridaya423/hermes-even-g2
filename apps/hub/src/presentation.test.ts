import {describe, expect, it} from "vitest";
import type {DurableEvent, SessionSummary} from "@hermes-g2/protocol";
import {approvalDisplay, buildGlassView, detailPages, gestureIsDuplicate, paginateText, runtimeDisplay} from "./presentation";
import type {ViewState} from "./state";

const session: SessionSummary = {
  id: "session-1234567890",
  title: "Ship Hermes",
  source: "desktop",
  model: "claude-sonnet",
  provider: "anthropic",
  workspace: "/Users/hriday/honey/hermes",
  executionReady: true,
  state: "busy",
  updatedAt: "2026-08-12T10:00:00Z",
  pinned: true,
};

const baseState: ViewState = {
  sessions: [session],
  selected: 0,
  mode: "default",
  detailPage: 0,
  decisionIndex: 0,
  connected: true,
  cursor: 4,
  pending: [],
  activeRuns: [{runId: "run-1", sessionId: session.id, initiatedByG2: true, status: "started", updatedAt: "now"}],
  latestEvents: {},
  history: {},
  phase: "ready",
  reconciled: true,
  runtime: {bridge: true, hermes: true, coreReady: true, guiReady: false, tailscale: true, stt: true},
};

describe("Hermes G2 presentation model", () => {
  it("keeps every character of a long response across bounded pages", () => {
    const response = `${"A".repeat(517)}\nThen run the tests.`;
    const pages = paginateText(response, 180);
    expect(pages.length).toBeGreaterThan(2);
    expect(pages.every((page) => page.length <= 180)).toBe(true);
    expect(pages.join("\n")).toContain("Then run the tests.");
    expect(pages.join("")).toContain("A".repeat(517));
  });

  it("prioritizes a meaningful busy checkpoint over stale answer text", () => {
    const event: DurableEvent = {
      protocolVersion: "1.0",
      eventId: "event-1",
      cursor: 5,
      kind: "tool.started",
      timestamp: "2026-08-12T10:01:00Z",
      source: "hermes",
      sessionId: session.id,
      runId: "run-1",
      payload: {tool: "shell", command: "npm test", status: "running"},
    };
    const view = buildGlassView({...baseState, latestEvents: {[session.id]: [event]}});
    expect(view.body).toContain("SHELL");
    expect(view.body).toContain("RUNNING");
    expect(view.footer).toContain("CORE");
  });

  it("makes sensitive approvals phone-only and preserves the server choice order", () => {
    const approval = approvalDisplay({
      requestId: "approval-1", sessionId: session.id, runId: "run-1", tool: "secrets", sensitive: true, destructive: false,
      choices: ["deny", "once"], command: "cat ~/.env",
    }, 1, "approval");
    expect(approval.phoneOnly).toBe(true);
    expect(approval.body).toContain("PHONE REQUIRED");
    expect(approval.choices).toEqual(["DENY", "ONCE"]);
    expect(approval.body).not.toContain("cat ~/.env");
  });

  it("surfaces readiness tiers without implying GUI tools are available", () => {
    expect(runtimeDisplay({bridge: true, hermes: true, coreReady: true, guiReady: false, tailscale: true, stt: true})).toBe("LINK READY · CORE READY · GUI WAIT");
    expect(runtimeDisplay({bridge: true, hermes: false, coreReady: false, guiReady: false, tailscale: true, stt: false, reason: "Hermes offline"})).toBe("LINK WAIT · CORE WAIT · GUI WAIT");
  });

  it("debounces duplicate press events while allowing a distinct gesture", () => {
    expect(gestureIsDuplicate({type: 0, at: 1000}, 0, 1100)).toBe(true);
    expect(gestureIsDuplicate({type: 0, at: 1000}, 0, 1300)).toBe(false);
    expect(gestureIsDuplicate({type: 0, at: 1000}, 1, 1100)).toBe(false);
  });

  it("adds full response, tool, subagent and provenance pages", () => {
    const answer = {id: "m-1", sessionId: session.id, role: "assistant" as const, content: "Done."};
    const pages = detailPages({...baseState, history: {[session.id]: [answer]}});
    expect(pages.map((page) => page.title)).toEqual(["FULL ANSWER", "TOOLS", "SUBAGENTS", "PROVENANCE"]);
    expect(pages[0].text).toBe("Done.");
    expect(pages[3].text).toContain("DESKTOP");
  });
});
