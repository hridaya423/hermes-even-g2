import {useEffect, useMemo, useRef, useState} from "react";
import {CreateStartUpPageContainer, RebuildPageContainer, TextContainerProperty, waitForEvenAppBridge} from "@evenrealities/even_hub_sdk";
import type {AgentAction, ApprovalChoice, ApprovalRequest, DurableEvent, SessionSummary} from "@hermes-g2/protocol";
import {BridgeApi, loadCredentials, saveCredentials, type Credentials} from "./api";
import {beginRecording, bindTranscript, cycleSession, visibleSession, type ViewState} from "./state";

type GlassBridge = Awaited<ReturnType<typeof waitForEvenAppBridge>>;
const initial: ViewState = {sessions: [], selected: 0, mode: "default", detailPage: 0, decisionIndex: 0, connected: false, cursor: Number(localStorage.getItem("hermes-g2.cursor") ?? 0), pending: [], latestEvents: {}};

export default function App() {
  const [credentials, setCredentials] = useState<Credentials | undefined>(loadCredentials());
  const [state, setState] = useState(initial);
  const stateRef = useRef(state); stateRef.current = state;
  const bridgeRef = useRef<GlassBridge | undefined>(undefined);
  const pcmRef = useRef<Uint8Array[]>([]);
  const session = visibleSession(state);
  const approval = state.pending.find((item) => item.sessionId === session?.id);
  const events = session ? state.latestEvents[session.id] ?? [] : [];
  const latest = events.at(-1);
  const api = useMemo(() => credentials ? new BridgeApi(credentials) : undefined, [credentials]);

  useEffect(() => {
    if (!api) return;
    let stop = () => {};
    void api.snapshot().then((snapshot) => {
      const sessions = Array.isArray(snapshot.sessions) ? snapshot.sessions : snapshot.sessions.items ?? [];
      const selectedId = localStorage.getItem("hermes-g2.selected");
      const selected = Math.max(0, sessions.findIndex((item) => item.id === selectedId));
      setState((value) => ({...value, sessions: orderSessions(sessions).slice(0, 10), selected, cursor: snapshot.cursor, connected: true}));
      stop = api.channel(snapshot.cursor, receiveEvent, (connected) => setState((value) => ({...value, connected})));
    }).catch((error) => setState((value) => ({...value, notice: String(error), connected: false})));
    return () => stop();
  }, [api]);

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
    localStorage.setItem("hermes-g2.cursor", String(event.cursor));
    setState((value) => {
      const sessionEvents = event.sessionId ? [...(value.latestEvents[event.sessionId] ?? []), event].slice(-80) : [];
      const pending = event.kind === "approval.required" ? upsertApproval(value.pending, event.payload as ApprovalRequest) : event.kind === "approval.resolved" ? value.pending.filter((item) => item.requestId !== (event.payload as ApprovalRequest).requestId) : value.pending;
      return {...value, cursor: event.cursor, pending, latestEvents: event.sessionId ? {...value.latestEvents, [event.sessionId]: sessionEvents} : value.latestEvents};
    });
  }

  async function handleGlassEvent(event: unknown): Promise<void> {
    const value = event as Record<string, any>;
    const pcm = value.audioEvent?.audioPcm ?? value.audioEvent?.pcm ?? value.audioPcm;
    if (pcm) { pcmRef.current.push(pcm instanceof Uint8Array ? pcm : new Uint8Array(pcm)); return; }
    const type = value.textEvent?.eventType ?? value.listEvent?.eventType ?? value.sysEvent?.eventType ?? value.jsonData?.eventType;
    if (type === 3) return setState((current) => ({...current, mode: current.mode === "detail" ? "default" : "detail", detailPage: 0}));
    if (type === 1) return navigate(-1);
    if (type === 2) return navigate(1);
    if (type === 0 || type === 4) await press();
  }

  function navigate(delta: number): void {
    setState((current) => {
      if (current.mode === "approval" || current.mode === "confirmation") return {...current, decisionIndex: Math.max(0, Math.min((approval?.choices.length ?? 1) - 1, current.decisionIndex + delta))};
      if (current.mode === "detail") return {...current, detailPage: Math.max(0, Math.min(3, current.detailPage + delta))};
      if (current.mode === "transcript" && delta > 0) return {...beginRecording(current), notice: "Record again"};
      if (current.mode === "transcript") return {...current, mode: "default", transcript: undefined, recordingSessionId: undefined, notice: "Cancelled"};
      const next = cycleSession(current, delta); localStorage.setItem("hermes-g2.selected", visibleSession(next)?.id ?? ""); return next;
    });
  }

  async function press(): Promise<void> {
    const current = stateRef.current;
    if (!api || !session) return;
    if (current.mode === "approval") return setState((value) => ({...value, mode: "confirmation"}));
    if (current.mode === "confirmation" && approval) return sendApproval(approval, approval.choices[current.decisionIndex]);
    if (approval) return setState((value) => ({...value, mode: "approval", decisionIndex: 0}));
    if (current.mode === "transcript" && current.transcript) return sendPrompt(current.transcript.text, current.transcript.sessionId);
    if (current.mode === "recording") return stopRecording();
    pcmRef.current = [];
    setState(beginRecording(current));
    await bridgeRef.current?.audioControl(true);
  }

  async function stopRecording(): Promise<void> {
    await bridgeRef.current?.audioControl(false);
    const target = stateRef.current.recordingSessionId;
    if (!api || !target || !pcmRef.current.length) return setState((value) => ({...value, mode: "default", notice: "NO SPEECH"}));
    try {
      const transcript = await api.audio(target, concat(pcmRef.current));
      setState((value) => bindTranscript(value, {text: transcript.transcript, duration: transcript.duration, confidence: transcript.confidence, sessionId: transcript.sessionId}));
    } catch (error) { setState((value) => ({...value, mode: "default", notice: String(error)})); }
  }

  async function sendPrompt(text: string, sessionId: string): Promise<void> {
    if (!api || !credentials) return;
    const target = stateRef.current.sessions.find((item) => item.id === sessionId);
    const action: AgentAction = {kind: target?.state === "busy" ? "queuePrompt" : "prompt", deviceId: credentials.deviceId, idempotencyKey: crypto.randomUUID(), sessionId, createdAt: new Date().toISOString(), payload: {text}};
    try { await api.action(action); setState((value) => ({...value, mode: "default", transcript: undefined, recordingSessionId: undefined, notice: target?.state === "busy" ? "QUEUED" : "SENT"})); }
    catch (error) { setState((value) => ({...value, notice: String(error)})); }
  }

  async function sendApproval(request: ApprovalRequest, choice?: ApprovalChoice): Promise<void> {
    if (!api || !credentials || !choice) return;
    const kind = {once: "approveOnce", session: "approveSession", always: "approveAlways", deny: "deny"}[choice] as AgentAction["kind"];
    const action: AgentAction = {kind, deviceId: credentials.deviceId, idempotencyKey: crypto.randomUUID(), sessionId: request.sessionId, runId: request.runId, expectedState: "awaiting_approval", createdAt: new Date().toISOString(), payload: {requestId: request.requestId}};
    try { await api.action(action); setState((value) => ({...value, mode: "default", notice: choice.toUpperCase()})); } catch (error) { setState((value) => ({...value, mode: "default", notice: `STALE: ${String(error)}`})); }
  }

  if (!credentials) return <Pairing onSave={(value) => {saveCredentials(value); setCredentials(value);}} />;
  return <main className="preview"><section className="screen">
    <Header session={session} connected={state.connected}/>
    <Body state={state} session={session} approval={approval} latest={latest}/>
    <footer><span>{state.sessions.length ? `${state.selected + 1}/${state.sessions.length}` : "0/0"}</span><span>{state.pending.length} PENDING</span><span>{state.notice ?? session?.state.toUpperCase() ?? "OFFLINE"}</span></footer>
  </section><button className="reset" onClick={() => {localStorage.removeItem("hermes-g2.credentials"); setCredentials(undefined);}}>Forget device</button></main>;
}

function Header({session, connected}: {session?: SessionSummary; connected: boolean}) { return <header><b>HERMES</b><span>{session?.title ?? "NO SESSION"}<small>{session ? `${session.source} · ${shortId(session.id)}` : "Pair or create a session"}</small></span><span>{session?.model ?? "—"}<small>{connected ? "CONNECTED" : "OFFLINE"}</small></span></header>; }
function Body({state, session, approval, latest}: {state: ViewState; session?: SessionSummary; approval?: ApprovalRequest; latest?: DurableEvent}) {
  if (state.transcript) return <section className="body transcript"><label>VOICE TRANSCRIPT</label><p>“{state.transcript.text}”</p><aside>DESTINATION · {session?.title} · {shortId(state.transcript.sessionId)}</aside><strong>PRESS SEND · ↓ CANCEL · ↑ AGAIN</strong></section>;
  if (approval) { const choice = approval.choices[state.decisionIndex]; return <section className="body"><label>ACTION REQUIRED · {approval.tool}</label><p>{approval.command ?? approval.destination ?? "Hermes requests permission to continue."}</p><ul>{approval.choices.map((item, index) => <li className={index === state.decisionIndex ? "selected" : ""} key={item}>{index === state.decisionIndex ? "■" : "□"} {item.toUpperCase()}</li>)}</ul><strong>{state.mode === "confirmation" ? `PRESS AGAIN TO CONFIRM ${choice?.toUpperCase()}` : "SWIPE CHOICE · PRESS SELECT"}</strong></section>; }
  if (state.mode === "detail") return <Detail state={state} session={session}/>;
  if (state.mode === "recording") return <section className="body"><label>LISTENING</label><p>Recording for {session?.title}. The destination is now locked.</p><strong>PRESS TO STOP · 45 SECOND MAX</strong></section>;
  return <section className="body"><label>{session?.state === "busy" ? "CURRENT CHECKPOINT" : latest?.kind?.toUpperCase() ?? "LATEST ANSWER"}</label><p>{summary(latest, session)}</p><aside>{session?.executionReady ? "EXECUTION READY" : "UNBOUND · WORKSPACE TOOLS BLOCKED"}</aside><strong>PRESS TO SPEAK · DOUBLE PRESS DETAIL</strong></section>;
}
function Detail({state, session}: {state: ViewState; session?: SessionSummary}) { const events = session ? state.latestEvents[session.id] ?? [] : []; const pages = [{title: "FULL ANSWER", text: [...events].reverse().find((event) => event.kind === "message.completed")?.payload}, {title: "TOOLS", text: events.filter((event) => event.kind.startsWith("tool.")).slice(-6).map((event) => event.payload)}, {title: "SUBAGENTS", text: events.filter((event) => event.kind.startsWith("subagent.")).slice(-6).map((event) => event.payload)}, {title: "PROVENANCE", text: session}]; const page = pages[state.detailPage]; return <section className="body detail"><label>{page.title} · {state.detailPage + 1}/4</label><pre>{format(page.text)}</pre><strong>SWIPE PAGES · DOUBLE PRESS BACK</strong></section>; }
function Pairing({onSave}: {onSave: (credentials: Credentials) => void}) { const [origin, setOrigin] = useState("https://mac-mini.tailnet.ts.net/hermes-g2"); const [deviceId, setDevice] = useState(""); const [credential, setCredential] = useState(""); return <main className="pairing"><h1>Hermes G2</h1><p>Enter the scoped Hub credential from the Android pairing flow. The Hermes master key never belongs here.</p><input aria-label="Bridge origin" value={origin} onChange={(event) => setOrigin(event.target.value.replace(/\/$/, ""))}/><input aria-label="Device ID" placeholder="Device ID" value={deviceId} onChange={(event) => setDevice(event.target.value)}/><input aria-label="Device credential" type="password" placeholder="Device credential" value={credential} onChange={(event) => setCredential(event.target.value)}/><button disabled={!origin || !deviceId || !credential} onClick={() => onSave({origin, deviceId, credential})}>Connect private bridge</button></main>; }

function renderGlass(state: ViewState) { const session = visibleSession(state); const approval = state.pending.find((item) => item.sessionId === session?.id); const latest = session ? state.latestEvents[session.id]?.at(-1) : undefined; const header = session ? `HERMES  ${session.title.slice(0, 24)}  ${shortId(session.id)}  ${state.connected ? "●" : "○"}` : "HERMES  NO SESSION"; let body = summary(latest, session); if (state.mode === "recording") body = `LISTENING\n\nDestination locked: ${session?.title}\n${shortId(state.recordingSessionId ?? "")}`; if (state.transcript) body = `CONFIRM DESTINATION\n${session?.title} · ${shortId(state.transcript.sessionId)}\n\n${state.transcript.text}\n\nPRESS SEND · ↓ CANCEL · ↑ AGAIN`; if (approval) body = `APPROVAL · ${approval.tool}\n${approval.command ?? approval.destination ?? "Action requires permission"}\n\n${approval.choices.map((choice, index) => `${index === state.decisionIndex ? "■" : "□"} ${choice.toUpperCase()}`).join("   ")}\n\n${state.mode === "confirmation" ? "PRESS AGAIN TO CONFIRM" : "SWIPE · PRESS SELECT"}`; const item = (id: number, name: string, y: number, height: number, content: string, capture = 0) => new TextContainerProperty({containerID: id, containerName: name, xPosition: 12, yPosition: y, width: 552, height, borderWidth: 0, borderColor: 15, borderRadius: 0, paddingLength: 2, content, isEventCapture: capture}); return {containerTotalNum: 3, textObject: [item(1, "header", 8, 34, header), item(2, "body", 50, 180, body, 1), item(3, "footer", 238, 38, `${state.selected + 1}/${Math.max(1, state.sessions.length)}   ${state.pending.length} PENDING   ${state.notice ?? session?.state.toUpperCase() ?? "OFFLINE"}`)]}; }
function summary(event?: DurableEvent, session?: SessionSummary): string { const payload = event?.payload as Record<string, unknown> | string | undefined; if (typeof payload === "string") return payload.slice(0, 600); return String(payload?.summary ?? payload?.message ?? payload?.content ?? session?.latestAnswer ?? (session ? "Press to speak a continuation into this exact session." : "Pair the private bridge to begin.")); }
function format(value: unknown): string { return typeof value === "string" ? value : JSON.stringify(value ?? "No activity yet.", null, 2); }
function shortId(value: string): string { return value.length > 12 ? `${value.slice(0, 5)}…${value.slice(-4)}` : value; }
function concat(chunks: Uint8Array[]): Uint8Array { const output = new Uint8Array(chunks.reduce((sum, item) => sum + item.length, 0)); let offset = 0; for (const chunk of chunks) { output.set(chunk, offset); offset += chunk.length; } return output; }
function orderSessions(items: SessionSummary[]): SessionSummary[] { return [...items].sort((a, b) => Number(b.pinned) - Number(a.pinned) || b.updatedAt.localeCompare(a.updatedAt)); }
function upsertApproval(items: ApprovalRequest[], incoming: ApprovalRequest): ApprovalRequest[] { return [incoming, ...items.filter((item) => item.requestId !== incoming.requestId)]; }
function isEvenHost(): boolean { return Boolean((window as any).flutter_inappwebview || (window as any).evenAppBridge || /EvenApp/i.test(navigator.userAgent)); }
