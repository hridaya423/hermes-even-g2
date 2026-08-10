import test from "node:test";
import assert from "node:assert/strict";
import { PROTOCOL_VERSION, type AgentAction, type AttachmentUpload } from "./index.js";

test("protocol is explicitly versioned", () => assert.equal(PROTOCOL_VERSION, "1.0"));

test("actions carry an exact destination", () => {
  const action: AgentAction = {kind: "prompt", deviceId: "g2", idempotencyKey: "k", sessionId: "s", createdAt: new Date(0).toISOString(), payload: {text: "continue"}};
  assert.equal(action.sessionId, "s");
});

test("attachment receipts preserve an opaque ID and exact session binding", () => {
  const attachment: AttachmentUpload = {attachmentId: "a", sessionId: "s", name: "file.pdf", mediaType: "application/pdf", size: 10, sha256: "digest"};
  assert.deepEqual([attachment.attachmentId, attachment.sessionId], ["a", "s"]);
});
