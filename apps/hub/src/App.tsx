import {useEffect, useMemo, useRef, useState} from "react";
import {CreateStartUpPageContainer, RebuildPageContainer, TextContainerProperty, waitForEvenAppBridge} from "@evenrealities/even_hub_sdk";
import type {AgentAction, AgentMessage, ApprovalChoice, ApprovalRequest, DurableEvent, SessionSummary} from "@hermes-g2/protocol";
import {BridgeApi, loadCredentials, saveCredentials, type Credentials} from "./api";
import {loadHubPersistence, persistViewState, applyRunEvent, beginRecording, beginStopConfirmation, bindTranscript, cycleSession, detailContent, setReadingPosition, visibleSession, type StopTarget, type ViewState} from "./state";
import {HubTransport, StaleActionError, TransportNotReadyError, TransportUnavailableError, UncertainActionError} from "./transport";

type GlassBridge = Awaited<ReturnType<typeof waitForEvenAppBridge>>;
const persisted = loadHubPersistence();
const initial: ViewState = {sessions: [], selected: 0, mode: "default", detailPage: 0, decisionIndex: 0, connected: false, cursor: persisted.cursor, pending: [], activeRuns: [], latestEvents: {}, history: {}, phase: "offline", reconciled: false, readingPositions: persisted.readingPositions, recordingSessionId: persisted.recordingSessionId, transcriptDestination: persisted.transcriptDestination};

export default function App() {
  const [credentials, setCredentials] = useState<Credentials | undefined>(loadCredentials());
  const [state, setState] = useState(initial);
  const stateRef = useRef(state); stateRef.current = state;
  const bridgeRef = useRef<GlassBridge | undefined>(undefined);
  const transportRef = useRef<HubTransport | undefined>(undefined);
  const pcmRef = useRef<Uint8Array[]>([]);
  const session = visibleSession(state);
  const approval = state.pending.find((item) => item.sessionId === session?.id);
  const events = session ? state.latestEvents[session.id] ?? [] : [];
  const latest = events.at(-1);
  const api = useMemo(() => credentials ? new BridgeApi(credentials) : undefined, [credentials]);

  useEffect(() => {
    if (!api) return;
    let cancelled = false;
    const transport = new HubTransport(api, {
      storage: typeof localStorage === "undefined" ? undefined : localStorage,
      onSnapshot: (snapshot) => {
        if (cancelled) return;
        const sessions = Array.isArray(snapshot.sessions) ? snapshot.sessions : snapshot.sessions.items ?? [];
        const selectedId = loadHubPersistence().selectedSessionId;
        const carousel = [...orderSessions(sessions).slice(0, 9), newSessionRow()];
        const selected = Math.max(0, carousel.findIndex((item) => item.id === selectedId));
        setState((value) => ({...value, sessions: carousel, selected: selected < 0 ? 0 : selected, detailPage: Math.min(value.readingPositions?.[carousel[selected < 0 ? 0 : selected]?.id] ?? 0, 3), cursor: snapshot.cursor, activeRuns: snapshot.activeRuns ?? [], pending: snapshot.pendingApprovals ?? [], latestEvents: {}, runtime: snapshot.runtime, hermes: snapshot.hermes}));
      },
      onEvent: receiveEvent,
      onStatus: (status) => {
        if (cancelled) return;
        setState((value) => ({...value, phase: status.phase, reconciled: status.reconciled, connected: status.connected, cursor: status.cursor, outbox: status.outbox, notice: status.error ? status.error : value.notice}));
      },
    });
    transportRef.current = transport;
    void transport.start().catch((error) => {
      if (!cancelled) setState((value) => ({...value, phase: "offline", reconciled: false, notice: String(error), connected: false}));
    });
    return () => { cancelled = true; transport.stop(); if (transportRef.current === transport) transportRef.current = undefined; };
  }, [api]);

  useEffect(() => {
    persistViewState(state, typeof localStorage === "undefined" ? undefined : localStorage, state.outbox?.map((item) => item.idempotencyKey) ?? []);
  }, [state.cursor, state.selected, state.detailPage, state.readingPositions, state.recordingSessionId, state.transcriptDestination, state.outbox]);

  useEffect(() => {
    if (!api || !session?.id || session.id === "__new__" || state.history[session.id]) return;
    let cancelled = false;
    void api.messages(session.id).then((page) => {
      if (!cancelled) setState((value) => ({...value, history: {...value.history, [session.id]: page.data}}));
    }).catch((error) => {
      if (!cancelled) setState((value) => ({...value, notice: `HISTORY: ${String(error)}`}));
    });
    return () => { cancelled = true; };
  }, [api, session?.id, state.history]);

  useEffect(() => {
    if (!isEvenHost()) return;
    let off: (() => void) | undefined;
    void waitForEvenAppBridge().then(async (bridge) => {
      bridgeRef.current = bridge;
      await bridge.createStartUpPageContainer(new CreateStartUpPageContainer(renderGlass(stateRef.current)));
      off = bridge.onEvenHubEvent((event) => void handleGlassEvent(event));
    });
    return () => off?.();
  }, []);

  useEffect(() => { void bridgeRef.current?.rebuildPageContainer(new RebuildPageContainer(renderGlass(state))); }, [state]);

  function receiveEvent(event: DurableEvent): void {
    setState((value) => {
      const sessionEvents = event.sessionId ? [...(value.latestEvents[event.sessionId] ?? []), event].slice(-80) : [];
      const pending = event.kind === "approval.required" ? upsertApproval(value.pending, event.payload as ApprovalRequest) : event.kind === "approval.resolved" ? value.pending.filter((item) => item.requestId !== (event.payload as ApprovalRequest).requestId) : value.pending;
      const next = {...value, cursor: event.cursor, pending, latestEvents: event.sessionId ? {...value.latestEvents, [event.sessionId]: sessionEvents} : value.latestEvents};
      return applyRunEvent(next, event);
    });
  }

  async function handleGlassEvent(event: unknown): Promise<void> {
    const value = event as Record<string, any>;
    const pcm = value.audioEvent?.audioPcm ?? value.audioEvent?.pcm ?? value.audioPcm;
    if (pcm) { pcmRef.current.push(pcm instanceof Uint8Array ? pcm : new Uint8Array(pcm)); return; }
    const type = value.textEvent?.eventType ?? value.listEvent?.eventType ?? value.sysEvent?.eventType ?? value.jsonData?.eventType;
    if (type === 3) return setState((current) => { const entering = current.mode !== "detail"; const currentSession = visibleSession(current); return {...current, mode: entering ? "detail" : "default", detailPage: entering ? current.readingPositions?.[currentSession?.id ?? ""] ?? 0 : current.detailPage, stopTarget: undefined}; });
    if (type === 1) return navigate(-1);
    if (type === 2) return navigate(1);
    if (type === 0 || type === 4) await press();
  }

  function navigate(delta: number): void {
    setState((current) => {
      if (current.mode === "approval" || current.mode === "confirmation") return {...current, decisionIndex: Math.max(0, Math.min((approval?.choices.length ?? 1) - 1, current.decisionIndex + delta))};
      if (current.mode === "stopConfirmation") return delta < 0 ? {...current, mode: "detail", stopTarget: undefined, notice: "CANCELLED"} : current;
      if (current.mode === "detail") return setReadingPosition(current, Math.max(0, Math.min(3, current.detailPage + delta)));
      if (current.mode === "transcript" && delta > 0) return {...beginRecording(current), notice: "Record again"};
      if (current.mode === "transcript") return {...current, mode: "default", transcript: undefined, recordingSessionId: undefined, transcriptDestination: undefined, notice: "Cancelled"};
      return cycleSession(current, delta);
    });
  }

  async function press(): Promise<void> {
    const current = stateRef.current;
    if (!api || !session) return;
    if (!current.reconciled) return setState((value) => ({...value, notice: "SYNCING SNAPSHOT"}));
    if (session.id === "__new__") return createSession();
    if (current.mode === "approval") return setState((value) => ({...value, mode: "confirmation"}));
    if (current.mode === "confirmation" && approval) return sendApproval(approval, approval.choices[current.decisionIndex]);
    if (approval) return setState((value) => ({...value, mode: "approval", decisionIndex: 0}));
    if (current.mode === "stopConfirmation" && current.stopTarget) return sendStop(current.stopTarget);
    if (current.mode === "detail") return setState((value) => { const next = beginStopConfirmation(value); return next === value ? {...value, notice: "NO ACTIVE RUN"} : next; });
    if (current.mode === "transcript" && current.transcript) return sendPrompt(current.transcript.text, current.transcript.sessionId);
    if (current.mode === "recording") return stopRecording();
    pcmRef.current = [];
    setState(beginRecording(current));
    await bridgeRef.current?.audioControl(true);
  }

  async function stopRecording(): Promise<void> {
    await bridgeRef.current?.audioControl(false);
    const target = stateRef.current.recordingSessionId;
    if (!api || !target || !pcmRef.current.length) return setState((value) => ({...value, mode: "default", recordingSessionId: undefined, transcriptDestination: undefined, notice: "NO SPEECH"}));
    try {
      const transcript = await api.audio(target, concat(pcmRef.current));
      setState((value) => bindTranscript(value, {text: transcript.transcript, duration: transcript.duration, confidence: transcript.confidence, sessionId: transcript.sessionId}));
    } catch (error) { setState((value) => ({...value, mode: "default", recordingSessionId: undefined, transcriptDestination: undefined, notice: String(error)})); }
  }

  async function sendPrompt(text: string, sessionId: string): Promise<void> {
    if (!api || !credentials || !transportRef.current) return;
    const target = stateRef.current.sessions.find((item) => item.id === sessionId);
    const action: AgentAction = {kind: target?.state === "busy" ? "queuePrompt" : "prompt", deviceId: credentials.deviceId, idempotencyKey: idempotencyKey(), sessionId, createdAt: new Date().toISOString(), payload: {text}};
    try { await transportRef.current.dispatch(action); setState((value) => ({...value, mode: "default", transcript: undefined, recordingSessionId: undefined, transcriptDestination: undefined, notice: target?.state === "busy" ? "QUEUED" : "SENT"})); }
    catch (error) { setState((value) => ({...value, notice: actionErrorNotice(error, "PROMPT")})); }
  }

  async function sendStop(target: StopTarget): Promise<void> {
    if (!api || !credentials || !transportRef.current) return;
    const action: AgentAction = {kind: "stopRun", deviceId: credentials.deviceId, idempotencyKey: idempotencyKey(), sessionId: target.sessionId, runId: target.runId, expectedState: "running", createdAt: new Date().toISOString(), payload: {}};
    try { await transportRef.current.dispatch(action); setState((value) => ({...value, mode: "detail", stopTarget: undefined, notice: "STOP REQUESTED"})); }
    catch (error) { setState((value) => ({...value, mode: "detail", stopTarget: undefined, notice: `STOP FAILED: ${actionErrorNotice(error, "")}`})); }
  }

  async function createSession(): Promise<void> {
    if (!api || !credentials || !transportRef.current) return;
    try {
      const created = await transportRef.current.dispatch({kind: "createSession", deviceId: credentials.deviceId, idempotencyKey: idempotencyKey(), createdAt: new Date().toISOString(), payload: {title: "G2 session"}}) as {id?: string; session_id?: string};
      await transportRef.current.refresh();
      const snapshot = transportRef.current.status.snapshot;
      if (!snapshot) return;
      const sessions = Array.isArray(snapshot.sessions) ? snapshot.sessions : snapshot.sessions.items ?? [];
      const createdId = created.id ?? created.session_id;
      const carousel = [...orderSessions(sessions).slice(0, 9), newSessionRow()];
      setState((value) => ({...value, sessions: carousel, selected: Math.max(0, carousel.findIndex((item) => item.id === createdId)), notice: "SESSION CREATED"}));
    } catch (error) {
      setState((value) => ({...value, notice: actionErrorNotice(error, "CREATE SESSION")}));
    }
  }

  async function sendApproval(request: ApprovalRequest, choice?: ApprovalChoice): Promise<void> {
    if (!api || !credentials || !choice || !transportRef.current) return;
    const kind = {once: "approveOnce", session: "approveSession", always: "approveAlways", deny: "deny"}[choice] as AgentAction["kind"];
    const action: AgentAction = {kind, deviceId: credentials.deviceId, idempotencyKey: idempotencyKey(), sessionId: request.sessionId, runId: request.runId, expectedState: "awaiting_approval", createdAt: new Date().toISOString(), payload: {requestId: request.requestId}};
    try { await transportRef.current.dispatch(action); setState((value) => ({...value, mode: "default", notice: choice.toUpperCase()})); } catch (error) { setState((value) => ({...value, mode: "default", notice: actionErrorNotice(error, "STALE")})); }
  }

  if (!credentials) return <Pairing onSave={(value) => {saveCredentials(value); setCredentials(value);}} />;
  return <main className="preview"><section className="screen">
    <Header session={session} connected={state.connected}/>
    <Body state={state} session={session} approval={approval} latest={latest}/>
    <footer><span>{state.sessions.length ? `${state.selected + 1}/${state.sessions.length}` : "0/0"}</span><span>{state.pending.length} PENDING</span><span>{state.notice ?? outboxNotice(state.outbox) ?? session?.state.toUpperCase() ?? "OFFLINE"}</span></footer>
  </section><button className="reset" onClick={() => void forgetDevice()}>Forget device</button></main>;

  async function forgetDevice(): Promise<void> {
    if (!api) return;
    try {
      const result = await api.revokeDevice();
      if (!result.supported) {
        setState((value) => ({...value, notice: "REVOCATION UNSUPPORTED · DEVICE KEPT"}));
        return;
      }
      transportRef.current?.stop();
      if (typeof localStorage !== "undefined") {
        localStorage.removeItem("hermes-g2.credentials");
        localStorage.removeItem("hermes-g2.state");
        localStorage.removeItem("hermes-g2.outbox");
        localStorage.removeItem("hermes-g2.cursor");
        localStorage.removeItem("hermes-g2.selected");
      }
      setCredentials(undefined);
    } catch (error) {
      setState((value) => ({...value, notice: `REVOCATION FAILED · ${String(error)}`}));
    }
  }
}

function Header({session, connected}: {session?: SessionSummary; connected: boolean}) { return <header><b>HERMES</b><span>{session?.title ?? "NO SESSION"}<small>{session ? `${session.source} · ${shortId(session.id)}` : "Pair or create a session"}</small></span><span>{session?.model ?? "—"}<small>{connected ? "CONNECTED" : "OFFLINE"}</small></span></header>; }
function Body({state, session, approval, latest}: {state: ViewState; session?: SessionSummary; approval?: ApprovalRequest; latest?: DurableEvent}) {
  if (state.phase === "offline") return <section className="body"><label>OFFLINE</label><p>{state.notice ?? "Bridge is unavailable."}</p><strong>WAITING TO RECONNECT</strong></section>;
  if (!state.reconciled || state.phase === "reconciling") return <section className="body"><label>SYNCHRONIZING</label><p>Reconciling a fresh bridge snapshot before accepting actions.</p><strong>PLEASE WAIT</strong></section>;
  if (state.phase === "gap") return <section className="body"><label>REPLAY GAP</label><p>Bridge history compacted; rebuilding the session snapshot.</p><strong>PLEASE WAIT</strong></section>;
  if (state.phase === "reconnecting") return <section className="body"><label>RECONNECTING</label><p>Bridge connection lost. Pending actions remain retained.</p><strong>NO ACTIONS UNTIL READY</strong></section>;
  if (state.phase === "ready" && !state.connected) return <section className="body"><label>CONNECTING</label><p>Snapshot is current; opening the authenticated event stream.</p><strong>PLEASE WAIT</strong></section>;
  if (state.transcript) return <section className="body transcript"><label>VOICE TRANSCRIPT</label><p>“{state.transcript.text}”</p><aside>DESTINATION · {session?.title} · {shortId(state.transcript.sessionId)}</aside><strong>PRESS SEND · ↓ CANCEL · ↑ AGAIN</strong></section>;
  if (approval) { const choice = approval.choices[state.decisionIndex]; return <section className="body"><label>ACTION REQUIRED · {approval.tool}</label><p>{approval.command ?? approval.destination ?? "Hermes requests permission to continue."}</p><ul>{approval.choices.map((item, index) => <li className={index === state.decisionIndex ? "selected" : ""} key={item}>{index === state.decisionIndex ? "■" : "□"} {item.toUpperCase()}</li>)}</ul><strong>{state.mode === "confirmation" ? `PRESS AGAIN TO CONFIRM ${choice?.toUpperCase()}` : "SWIPE CHOICE · PRESS SELECT"}</strong></section>; }
  if (state.mode === "stopConfirmation" && state.stopTarget) return <section className="body"><label>CONFIRM RUN CANCELLATION</label><p>Stop run {shortId(state.stopTarget.runId)} in {session?.title} · {shortId(state.stopTarget.sessionId)}?</p><aside>The destination is locked and cannot change.</aside><strong>PRESS CONFIRM · ↓ CANCEL</strong></section>;
  if (state.mode === "detail") return <Detail state={state} session={session}/>;
  if (state.mode === "recording") return <section className="body"><label>LISTENING</label><p>Recording for {session?.title}. The destination is now locked.</p><strong>PRESS TO STOP · 45 SECOND MAX</strong></section>;
  return <section className="body"><label>{session?.state === "busy" ? "CURRENT CHECKPOINT" : latest?.kind?.toUpperCase() ?? "LATEST ANSWER"}</label><p>{summary(latest, session, session ? state.history[session.id] : undefined)}</p><aside>{session?.executionReady ? "EXECUTION READY" : "UNBOUND · WORKSPACE TOOLS BLOCKED"}</aside><strong>PRESS TO SPEAK · DOUBLE PRESS DETAIL</strong></section>;
}
function Detail({state, session}: {state: ViewState; session?: SessionSummary}) { const events = session ? state.latestEvents[session.id] ?? [] : []; const history = session ? state.history[session.id] ?? [] : []; const activeRun = session ? state.activeRuns.find((run) => run.sessionId === session.id) : undefined; const pages = [{title: "FULL ANSWER", text: latestAssistant(history)?.content ?? [...events].reverse().find((event) => event.kind === "message.completed")?.payload}, {title: "TOOLS", text: events.filter((event) => event.kind.startsWith("tool.")).slice(-6).map((event) => event.payload)}, {title: "SUBAGENTS", text: events.filter((event) => event.kind.startsWith("subagent.")).slice(-6).map((event) => event.payload)}, {title: "PROVENANCE", text: session}]; const pageIndex = Math.max(0, Math.min(pages.length - 1, state.detailPage)); const page = pages[pageIndex]; return <section className="body detail"><label>{page.title} · {pageIndex + 1}/4</label><pre>{format(page.text)}</pre><strong>{activeRun ? `PRESS TO STOP ${shortId(activeRun.runId)} · DOUBLE PRESS BACK` : "SWIPE PAGES · DOUBLE PRESS BACK"}</strong></section>; }
function Pairing({onSave}: {onSave: (credentials: Credentials) => void}) { const [origin, setOrigin] = useState("https://hridyas-mini.tail59dec9.ts.net/hermes-g2"); const [code, setCode] = useState(""); const [busy, setBusy] = useState(false); const [error, setError] = useState(""); const pair = async () => { setBusy(true); setError(""); try { onSave(await BridgeApi.exchange(origin, code)); } catch (value) { setError(String(value)); setBusy(false); } }; return <main className="pairing"><h1>Hermes G2</h1><p>Enter the 90-second, single-use Hub code from the Mac mini. The bridge issues this G2 its own revocable credential; the Hermes master key never enters the app.</p><input aria-label="Bridge origin" value={origin} onChange={(event) => setOrigin(event.target.value.replace(/\/$/, ""))}/><input aria-label="Pairing code" inputMode="numeric" placeholder="6-digit pairing code" value={code} onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))}/>{error && <p role="alert">{error}</p>}<button disabled={busy || !origin.startsWith("https://") || code.length !== 6} onClick={() => void pair()}>{busy ? "Pairing…" : "Pair private G2"}</button></main>; }

function renderGlass(state: ViewState) { const session = visibleSession(state); const approval = state.pending.find((item) => item.sessionId === session?.id); const latest = session ? state.latestEvents[session.id]?.at(-1) : undefined; const header = session ? `HERMES  ${session.title.slice(0, 24)}  ${shortId(session.id)}  ${state.connected ? "●" : "○"}` : "HERMES  NO SESSION"; let body = state.phase === "offline" ? `OFFLINE\n\n${state.notice ?? "Bridge is unavailable."}` : !state.reconciled ? "SYNCHRONIZING\n\nWaiting for a fresh bridge snapshot." : state.phase === "gap" ? "REPLAY GAP\n\nRebuilding from a fresh snapshot." : state.phase === "reconnecting" ? "RECONNECTING\n\nPending actions retained." : state.phase === "ready" && !state.connected ? "CONNECTING\n\nOpening the authenticated event stream." : summary(latest, session, session ? state.history[session.id] : undefined); if (state.mode === "detail") body = detailContent(state); if (state.mode === "recording") body = `LISTENING\n\nDestination locked: ${session?.title}\n${shortId(state.recordingSessionId ?? "")}`; if (state.transcript) body = `CONFIRM DESTINATION\n${session?.title} · ${shortId(state.transcript.sessionId)}\n\n${state.transcript.text}\n\nPRESS SEND · ↓ CANCEL · ↑ AGAIN`; if (approval) body = `APPROVAL · ${approval.tool}\n${approval.command ?? approval.destination ?? "Action requires permission"}\n\n${approval.choices.map((choice, index) => `${index === state.decisionIndex ? "■" : "□"} ${choice.toUpperCase()}`).join("   ")}\n\n${state.mode === "confirmation" ? "PRESS AGAIN TO CONFIRM" : "SWIPE · PRESS SELECT"}`; if (state.mode === "stopConfirmation" && state.stopTarget) body = `CONFIRM RUN CANCELLATION\n${session?.title} · ${shortId(state.stopTarget.sessionId)}\nRun ${shortId(state.stopTarget.runId)}\n\nPRESS CONFIRM · ↓ CANCEL`; const item = (id: number, name: string, y: number, height: number, content: string, capture = 0) => new TextContainerProperty({containerID: id, containerName: name, xPosition: 12, yPosition: y, width: 552, height, borderWidth: 0, borderColor: 15, borderRadius: 0, paddingLength: 2, content, isEventCapture: capture}); return {containerTotalNum: 3, textObject: [item(1, "header", 8, 34, header), item(2, "body", 50, 180, body, 1), item(3, "footer", 238, 38, `${state.selected + 1}/${Math.max(1, state.sessions.length)}   ${state.pending.length} PENDING   ${state.notice ?? outboxNotice(state.outbox) ?? state.phase?.toUpperCase() ?? session?.state.toUpperCase() ?? "OFFLINE"}`)]}; }
function summary(event?: DurableEvent, session?: SessionSummary, history?: AgentMessage[]): string { if (session?.id === "__new__") return "Press to create a fresh Hermes session owned by Even G2."; const payload = event?.payload as Record<string, unknown> | string | undefined; if (typeof payload === "string") return payload.slice(0, 600); return String(payload?.summary ?? payload?.message ?? payload?.content ?? latestAssistant(history)?.content ?? session?.latestAnswer ?? (session ? "Press to speak a continuation into this exact session." : "Pair the private bridge to begin.")); }
function latestAssistant(history?: AgentMessage[]): AgentMessage | undefined { return history?.find((message) => message.role === "assistant" && message.content.trim()); }
function format(value: unknown): string { return typeof value === "string" ? value : JSON.stringify(value ?? "No activity yet.", null, 2); }
function shortId(value: string): string { return value.length > 12 ? `${value.slice(0, 5)}…${value.slice(-4)}` : value; }
function idempotencyKey(): string { return typeof crypto !== "undefined" && typeof crypto.randomUUID === "function" ? crypto.randomUUID() : `g2-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`; }
function outboxNotice(items: ViewState["outbox"]): string | undefined {
  const item = items?.find((value) => value.status === "uncertain" || value.status === "stale" || value.status === "retry" || value.status === "queued");
  return item ? `${item.status.toUpperCase()} ${shortId(item.idempotencyKey)}` : undefined;
}
function actionErrorNotice(error: unknown, prefix: string): string {
  const label = prefix ? `${prefix}: ` : "";
  if (error instanceof TransportNotReadyError) return `${label}SYNCING SNAPSHOT`;
  if (error instanceof TransportUnavailableError) return `${label}QUEUED FOR RETRY`;
  if (error instanceof StaleActionError) return `${label}STALE ACTION`;
  if (error instanceof UncertainActionError) return `${label}UNCERTAIN · RETRY TO CONFIRM`;
  return `${label}${String(error)}`;
}
function concat(chunks: Uint8Array[]): Uint8Array { const output = new Uint8Array(chunks.reduce((sum, item) => sum + item.length, 0)); let offset = 0; for (const chunk of chunks) { output.set(chunk, offset); offset += chunk.length; } return output; }
function orderSessions(items: SessionSummary[]): SessionSummary[] { return [...items].sort((a, b) => Number(b.pinned) - Number(a.pinned) || b.updatedAt.localeCompare(a.updatedAt)); }
function newSessionRow(): SessionSummary { return {id: "__new__", title: "NEW G2 SESSION", source: "even_g2", executionReady: true, state: "idle", updatedAt: "", pinned: false}; }
function upsertApproval(items: ApprovalRequest[], incoming: ApprovalRequest): ApprovalRequest[] { return [incoming, ...items.filter((item) => item.requestId !== incoming.requestId)]; }
function isEvenHost(): boolean { return Boolean((window as any).flutter_inappwebview || (window as any).evenAppBridge || /EvenApp/i.test(navigator.userAgent)); }
