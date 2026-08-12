import { describe, expect, it } from "vitest";
import { approvalDisplay } from "./presentation";
import type { ApprovalRequest } from "@hermes-g2/protocol";

const request = (overrides: Partial<ApprovalRequest> = {}): ApprovalRequest => ({
  requestId: "request-1",
  sessionId: "session-1",
  runId: "run-1",
  tool: "shell",
  choices: ["once", "session", "always", "deny"],
  destructive: false,
  sensitive: false,
  ...overrides,
});

describe("Hermes G2 approval presentation contract", () => {
  it("renders every server-offered choice in order and identifies the selected choice", () => {
    const expected = ["ONCE", "SESSION", "ALWAYS", "DENY"];
    expected.forEach((choice, index) => {
      const display = approvalDisplay(request(), index, "approval");
      expect(display.choices).toEqual(expected);
      expect(display.selected).toBe(choice);
      expect(display.body).toContain(choice);
    });
  });

  it("never renders a secret command or destination on glasses", () => {
    const display = approvalDisplay(request({
      tool: "secrets",
      sensitive: true,
      command: "cat ~/.env API_TOKEN=super-secret-value",
      destination: "/Users/private/project",
    }), 0, "confirmation");
    expect(display.phoneOnly).toBe(true);
    expect(display.body).toContain("PHONE REQUIRED");
    expect(display.body).not.toContain("super-secret-value");
    expect(display.body).not.toContain("API_TOKEN");
    expect(display.body).not.toContain("/Users/private/project");
  });

  it("requires a deliberate second confirmation for persistent or dangerous choices", () => {
    const persistent = approvalDisplay(request(), 1, "confirmation");
    expect(persistent.body).toContain("PRESS AGAIN TO CONFIRM SESSION");
    const dangerous = approvalDisplay(request({ destructive: true }), 0, "confirmation");
    expect(dangerous.body).toContain("DESTRUCTIVE");
    expect(dangerous.body).toContain("PRESS AGAIN TO CONFIRM ONCE");
  });
});
