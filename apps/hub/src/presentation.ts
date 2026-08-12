import type {ApprovalChoice, ApprovalRequest, AgentMessage, DurableEvent, RuntimeReadiness, SessionSummary} from "@hermes-g2/protocol";
import type {Mode, ViewState} from "./state";

export const GLASS_WIDTH = 576;
export const GLASS_HEIGHT = 288;
export const GLASS_BODY_WIDTH = 56;
export const GLASS_BODY_LINES = 7;
export const GLASS_DETAIL_PAGE_CHARS = 360;

export type PresentationPage = {title: string; text: string};
export type GlassView = {header: string; body: string; footer: string};
export type ApprovalDisplay = {body: string; choices: string[]; selected: string; phoneOnly: boolean};
export type GestureStamp = {type: number; at: number};

const TERMINAL_KINDS = new Set(["run.completed", "run.failed", "run.cancelled", "message.completed"]);
const CHECKPOINT_KINDS = new Set(["run.progress", "tool.started", "tool.completed", "tool.failed", "subagent.started", "subagent.completed"]);

export function paginateText(value: string, maxCharacters = GLASS_DETAIL_PAGE_CHARS): string[] {
  const text = value.trim();
  if (!text) return ["No activity yet."];
  const limit = Math.max(32, Math.floor(maxCharacters));
  const pages: string[] = [];
  let offset = 0;
  while (offset < text.length) {
    let end = Math.min(text.length, offset + limit);
    if (end < text.length) {
      const newline = text.lastIndexOf("\n", end);
      const space = text.lastIndexOf(" ", end);
      const softBreak = Math.max(newline, space);
      if (softBreak > offset + Math.floor(limit * 0.55)) end = softBreak + 1;
    }
    const page = text.slice(offset, end).trim();
    if (page) pages.push(page);
    offset = Math.max(end, offset + 1);
  }
  return pages.length ? pages : ["No activity yet."];
}

export function detailPages(state: ViewState): PresentationPage[] {
  const session = state.sessions[state.selected];
  if (!session) return [{title: "FULL ANSWER", text: "NO SESSION"}, {title: "TOOLS", text: "No activity yet."}, {title: "SUBAGENTS", text: "No activity yet."}, {title: "PROVENANCE", text: "Pair the private bridge first."}];
  const events = state.latestEvents[session.id] ?? [];
  const history = state.history[session.id] ?? [];
  const answer = latestAssistant(history)?.content
    ?? eventContent([...events].reverse().find((event) => event.kind === "message.completed"))
    ?? session.latestAnswer
    ?? "No completed answer yet.";
  const answerParts = paginateText(answer);
  const answerPages = answerParts.map((text, index) => ({title: answerParts.length === 1 ? "FULL ANSWER" : `FULL ANSWER ${index + 1}/${answerParts.length}`, text}));
  return [
    ...answerPages,
    {title: "TOOLS", text: eventList(events.filter((event) => event.kind.startsWith("tool.")))},
    {title: "SUBAGENTS", text: eventList(events.filter((event) => event.kind.startsWith("subagent.")))},
    {title: "PROVENANCE", text: provenance(session, state)},
  ];
}

export function approvalDisplay(request: ApprovalRequest, decisionIndex: number, mode: Mode): ApprovalDisplay {
  const choices = request.choices.map(choiceLabel);
  const selected = choices[Math.max(0, Math.min(request.choices.length - 1, decisionIndex))] ?? "DENY";
  if (request.sensitive) {
    return {
      phoneOnly: true,
      choices,
      selected,
      body: [
        "PHONE REQUIRED",
        `${request.tool.toUpperCase()} needs secret context.`,
        "Review and decide on the paired phone.",
        mode === "confirmation" ? "GLASSES CANNOT CONFIRM THIS" : "NO SECRET OR COMMAND SHOWN",
      ].join("\n"),
    };
  }
  const context = redactForDisplay(request.command ?? request.destination ?? request.rule ?? "Hermes requests permission to continue.");
  const warning = request.destructive ? "DESTRUCTIVE · CONFIRM CAREFULLY" : request.sensitive ? "SENSITIVE · REVIEW CAREFULLY" : "EXACT HERMES CHOICE";
  const selection = `SELECTED ${selected} · ${Math.max(0, Math.min(request.choices.length - 1, decisionIndex)) + 1}/${request.choices.length}`;
  return {
    phoneOnly: false,
    choices,
    selected,
    body: [
      `APPROVAL · ${request.tool.toUpperCase()}`,
      warning,
      context,
      choices.map((choice, index) => `${index === decisionIndex ? "■" : "□"} ${choice}`).join("  "),
      mode === "confirmation" ? `PRESS AGAIN TO CONFIRM ${selected}` : selection,
    ].join("\n"),
  };
}

export function runtimeDisplay(runtime?: RuntimeReadiness): string {
  const value = runtime ?? {bridge: false, hermes: false, coreReady: false, guiReady: false, tailscale: false, stt: false};
  return [
    value.bridge && value.hermes && value.tailscale ? "LINK READY" : "LINK WAIT",
    value.coreReady && value.hermes ? "CORE READY" : "CORE WAIT",
    value.guiReady ? "GUI READY" : "GUI WAIT",
  ].join(" · ");
}

export function gestureIsDuplicate(last: GestureStamp | undefined, type: number, at: number, debounceMs = 220): boolean {
  return Boolean(last && last.type === type && at >= last.at && at - last.at < debounceMs);
}

export function buildGlassView(state: ViewState): GlassView {
  const session = state.sessions[state.selected];
  const events = session ? state.latestEvents[session.id] ?? [] : [];
  const approval = state.pending.find((item) => item.sessionId === session?.id);
  const activeRun = session ? state.activeRuns.find((run) => run.sessionId === session.id) : undefined;
  const header = session ? [
    `HERMES · ${session.source.toUpperCase()} · ${shortId(session.id)}`,
    `${compact(session.title, 28)} · ${compact(session.model ?? session.provider ?? "DEFAULT", 16)}${workspaceLabel(session.workspace) ? ` · ${workspaceLabel(session.workspace)}` : ""}`,
  ].join("\n") : "HERMES · NO SESSION\nPair the private bridge or create a session";

  let body: string;
  if (state.phase === "offline") body = `OFFLINE\n${state.notice ?? "Bridge is unavailable."}\nWAITING TO RECONNECT`;
  else if (!state.reconciled || state.phase === "reconciling") body = "SYNCHRONIZING\nFresh bridge snapshot required before actions.\nPLEASE WAIT";
  else if (state.phase === "gap") body = "REPLAY GAP\nBridge history compacted; rebuilding snapshot.\nPLEASE WAIT";
  else if (state.phase === "reconnecting") body = "RECONNECTING\nPending actions remain retained.\nNO ACTIONS UNTIL READY";
  else if (state.phase === "ready" && !state.connected) body = "CONNECTING\nSnapshot is current; opening event stream.\nPLEASE WAIT";
  else if (state.transcript) body = ["CONFIRM DESTINATION", `${compact(session?.title ?? "NO SESSION", 30)} · ${shortId(state.transcript.sessionId)}`, state.transcript.text, "PRESS SEND · ↓ CANCEL · ↑ AGAIN"].join("\n");
  else if (approval) body = approvalDisplay(approval, state.decisionIndex, state.mode).body;
  else if (state.mode === "stopConfirmation" && state.stopTarget) body = ["CONFIRM RUN CANCELLATION", `${compact(session?.title ?? "NO SESSION", 28)} · ${shortId(state.stopTarget.sessionId)}`, `RUN ${shortId(state.stopTarget.runId)}`, "PRESS CONFIRM · ↓ CANCEL"].join("\n");
  else if (state.mode === "detail") {
    const pages = detailPages(state);
    const page = pages[Math.max(0, Math.min(pages.length - 1, state.detailPage))];
    body = `${page.title} · ${Math.max(0, Math.min(pages.length - 1, state.detailPage)) + 1}/${pages.length}\n${page.text}\n${activeRun ? `PRESS TO STOP ${shortId(activeRun.runId)} · DOUBLE PRESS BACK` : "SWIPE PAGES · DOUBLE PRESS BACK"}`;
  } else if (state.mode === "recording") body = ["LISTENING", `DESTINATION LOCKED · ${compact(session?.title ?? "NO SESSION", 28)}`, "PRESS TO STOP · 45 SECOND MAX"].join("\n");
  else body = defaultBody(state, session, events, activeRun);

  return {
    header,
    body: fitLines(body, GLASS_BODY_WIDTH, GLASS_BODY_LINES),
    footer: `${state.sessions.length ? `${state.selected + 1}/${state.sessions.length}` : "0/0"} · ${state.pending.length} PENDING · ${runtimeDisplay(state.runtime)}`,
  };
}

function defaultBody(state: ViewState, session: SessionSummary | undefined, events: DurableEvent[], activeRun: ViewState["activeRuns"][number] | undefined): string {
  if (!session) return "PAIR THE PRIVATE BRIDGE\nNo Hermes session is selected.\nPRESS TO PAIR";
  if (session.state === "queued") return "QUEUED\nA continuation is waiting for the active turn.\nPRESS TO SPEAK ANOTHER";
  if (session.state === "failed") return `FAILED\n${eventContent([...events].reverse().find((event) => event.kind === "run.failed")) ?? "Hermes stopped before completion."}\nPRESS TO SPEAK TO RETRY`;
  const checkpoint = latestCheckpoint(events);
  if (activeRun && checkpoint) return [`${checkpoint.label}`, checkpoint.text, `RUN ${shortId(activeRun.runId)} · IN PROGRESS`].join("\n");
  const answer = eventContent([...events].reverse().find((event) => TERMINAL_KINDS.has(event.kind))) ?? latestAssistant(state.history[session.id])?.content ?? session.latestAnswer ?? "PRESS TO SPEAK A CONTINUATION";
  return [`${session.state === "busy" ? "WORKING" : "LATEST ANSWER"}`, answer, session.executionReady ? "EXECUTION READY" : "UNBOUND · WORKSPACE TOOLS BLOCKED"].join("\n");
}

function latestCheckpoint(events: DurableEvent[]): {label: string; text: string} | undefined {
  const event = [...events].reverse().find((item) => CHECKPOINT_KINDS.has(item.kind));
  if (!event) return undefined;
  const payload = payloadRecord(event.payload);
  const tool = String(payload?.tool ?? payload?.toolName ?? payload?.name ?? "").trim();
  const status = String(payload?.status ?? event.kind.split(".")[1] ?? "progress").toUpperCase();
  const label = event.kind.startsWith("tool.") ? `TOOL · ${compact(tool || "RUNNING", 24).toUpperCase()}` : event.kind.startsWith("subagent.") ? `SUBAGENT · ${compact(tool || "ACTIVE", 22).toUpperCase()}` : `CHECKPOINT · ${status}`;
  return {label, text: compact(eventContent(event) ?? status, 96)};
}

function detailEvent(event: DurableEvent): string {
  const payload = payloadRecord(event.payload);
  const name = String(payload?.tool ?? payload?.toolName ?? payload?.name ?? event.kind.split(".")[1] ?? "event");
  const status = String(payload?.status ?? event.kind.split(".")[1] ?? "updated").toUpperCase();
  const content = eventContent(event);
  return `${name.toUpperCase()} · ${status}${content ? `\n${content}` : ""}`;
}

function eventList(events: DurableEvent[]): string {
  if (!events.length) return "No activity yet.";
  return events.slice(-6).map(detailEvent).join("\n\n");
}

function provenance(session: SessionSummary, state: ViewState): string {
  return [
    `SESSION ${shortId(session.id)}`,
    `SOURCE ${session.source.toUpperCase()}`,
    `MODEL ${session.model ?? session.provider ?? "DEFAULT"}`,
    `STATE ${session.state.toUpperCase()} · ${session.executionReady ? "EXECUTION READY" : "UNBOUND"}`,
    workspaceLabel(session.workspace) ? `WORKSPACE ${workspaceLabel(session.workspace)}` : "WORKSPACE NOT REPORTED",
    runtimeDisplay(state.runtime),
  ].join("\n");
}

function latestAssistant(history: AgentMessage[]): AgentMessage | undefined {
  return [...history].reverse().find((message) => message.role === "assistant" && message.content.trim());
}

function eventContent(event: DurableEvent | undefined): string | undefined {
  if (!event) return undefined;
  const payload = payloadRecord(event.payload);
  const candidate = payload?.summary ?? payload?.message ?? payload?.content ?? payload?.text ?? payload?.output ?? payload?.error ?? (typeof event.payload === "string" ? event.payload : undefined);
  return typeof candidate === "string" && candidate.trim() ? redactForDisplay(candidate.trim()) : undefined;
}

function payloadRecord(value: unknown): Record<string, unknown> | undefined {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
}

function choiceLabel(choice: ApprovalChoice): string { return choice === "once" ? "ONCE" : choice === "session" ? "SESSION" : choice === "always" ? "ALWAYS" : "DENY"; }

function redactForDisplay(value: string): string {
  return value.replace(/Bearer\s+[^\s]+/gi, "Bearer •••").replace(/(api[_-]?key|token|password|secret)\s*[:=]\s*[^\s,;]+/gi, "$1=•••");
}

function fitLines(value: string, width: number, maxLines: number): string {
  const lines = value.split("\n").flatMap((line) => wrapLine(line, width));
  if (lines.length <= maxLines) return lines.join("\n");
  const clipped = lines.slice(0, maxLines);
  clipped[maxLines - 1] = `${clipped[maxLines - 1].slice(0, Math.max(0, width - 1)).trimEnd()}…`;
  return clipped.join("\n");
}

function wrapLine(line: string, width: number): string[] {
  const value = line.trim();
  if (!value) return [""];
  const output: string[] = [];
  let offset = 0;
  while (offset < value.length) {
    let end = Math.min(value.length, offset + width);
    if (end < value.length) {
      const space = value.lastIndexOf(" ", end);
      if (space > offset + Math.floor(width * 0.55)) end = space;
    }
    output.push(value.slice(offset, end).trim());
    offset = Math.max(end, offset + 1);
  }
  return output;
}

function workspaceLabel(workspace?: string): string | undefined {
  if (!workspace) return undefined;
  const parts = workspace.split(/[\\/]/).filter(Boolean);
  return parts.length ? parts.slice(-2).join("/") : undefined;
}

function compact(value: string, max: number): string {
  const text = value.replace(/\s+/g, " ").trim();
  return text.length <= max ? text : `${text.slice(0, Math.max(0, max - 1)).trimEnd()}…`;
}

function shortId(value: string): string { return value.length > 12 ? `${value.slice(0, 5)}…${value.slice(-4)}` : value; }
