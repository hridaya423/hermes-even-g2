export const PROTOCOL_VERSION = "1.0" as const;

export type DeviceScope =
  | "sessions:read" | "sessions:write" | "audio:write" | "runs:control"
  | "approvals:write" | "jobs:write" | "attachments:write"
  | "devices:manage" | "diagnostics:read";

export type RuntimeReadiness = {
  bridge: boolean;
  hermes: boolean;
  coreReady: boolean;
  guiReady: boolean;
  tailscale: boolean;
  stt: boolean;
  summary?: boolean;
  reason?: string;
};

export type HermesCapabilities = {
  nativeSessions: boolean;
  sessionHistory: boolean;
  sessionStreaming: boolean;
  sessionRunControl: boolean;
  sessionApprovalResponse: boolean;
  jobs: boolean;
  models: boolean;
  skills: boolean;
  subagents: boolean;
  attachments: boolean;
  raw: Record<string, unknown>;
};

export type SessionSummary = {
  id: string;
  title: string;
  source: string;
  model?: string;
  provider?: string;
  parentSessionId?: string;
  workspace?: string;
  executionReady: boolean;
  state: "idle" | "busy" | "queued" | "failed" | "unbound";
  updatedAt: string;
  pinned: boolean;
  latestAnswer?: string;
};

export type AgentMessage = {
  id: string;
  sessionId: string;
  role: "user" | "assistant" | "tool" | "system";
  content: string;
  reasoning?: string;
  timestamp?: string;
  finishReason?: string;
  toolName?: string;
  toolCalls?: unknown[];
  tokenCount?: number;
};

export type ApprovalChoice = "once" | "session" | "always" | "deny";

export type ApprovalRequest = {
  requestId: string;
  sessionId: string;
  runId: string;
  tool: string;
  command?: string;
  destination?: string;
  rule?: string;
  destructive: boolean;
  sensitive: boolean;
  choices: ApprovalChoice[];
  expiresAt?: string;
};

export type EventKind =
  | "runtime.updated" | "session.created" | "session.updated" | "message.completed"
  | "run.started" | "run.progress" | "run.completed" | "run.failed" | "run.cancelled"
  | "tool.started" | "tool.completed" | "tool.failed"
  | "approval.required" | "approval.resolved"
  | "subagent.started" | "subagent.completed" | "job.updated"
  | "attention.created" | "attention.resolved";

export type DurableEvent<T = unknown> = {
  protocolVersion: typeof PROTOCOL_VERSION;
  eventId: string;
  cursor: number;
  kind: EventKind;
  timestamp: string;
  source: "bridge" | "hermes" | "plugin";
  sessionId?: string;
  runId?: string;
  payload: T;
};

export type ActionKind =
  | "createSession" | "forkSession" | "renameSession" | "setSessionModel" | "prompt" | "queuePrompt" | "stopRun"
  | "approveOnce" | "approveSession" | "approveAlways" | "deny"
  | "pinSession" | "unpinSession" | "runJob" | "pauseJob" | "resumeJob"
  | "acknowledge";

export type AgentAction = {
  kind: ActionKind;
  deviceId: string;
  idempotencyKey: string;
  sessionId?: string;
  runId?: string;
  expectedState?: string;
  createdAt: string;
  payload: Record<string, unknown>;
};

export type ChannelClientMessage =
  | { type: "hello"; afterCursor: number }
  | { type: "ack"; cursor: number }
  | { type: "ping" };
