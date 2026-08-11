# Hermes G2 Ultimate Product Design

## Objective

Make Hermes G2 a complete ambient Hermes client rather than a notification surface. The glasses should start and continue exact native sessions, show useful progress and full answers, handle safe approvals and interruption, and recover across phone, WebView, bridge, provider, and network restarts without the laptop.

## Product shape

The home surface stays conversation-first. It identifies the exact session and prioritizes approval, failure, checkpoint, queue state, or final answer. Voice creates a new session or continues the frozen visible session; busy sessions receive an explicit queued continuation. Transcript review names session, model, and immediate versus queued delivery.

Detail mode pages through the complete answer, tools, files, validation, attachments, subagents, jobs, and provenance. Android remains the secure controller for attachments, secrets, long structured input, model selection, jobs, skills, audit history, and TTS. Phone-side changes reconcile immediately to the glasses.

## Reliability contract

Hub transport uses snapshot-first boot, authenticated WSS, cursor replay, gap detection, resnapshot, exponential reconnect, acknowledgement, and an idempotent action outbox. State survives WebView recreation and preserves selected session, reading position, cursor, transcript destination, and pending action IDs.

Android owns a durable event repository consumed by both its foreground service and Compose UI. Reconciliation persists snapshots, process death restores state, and Doze/network recovery never spins or loses cursor progress.

## Security contract

Forgetting a device revokes it at the bridge. Pairing is rate-limited and single-use. Credentials are never logged or placed in URLs. Attachments bind an existing target session, enforce per-file/device/aggregate quotas and TTL, use streaming limits, and are removed after consumption or expiry. Glasses-safe event payloads exclude secret or lock-screen-sensitive content.

## Hermes integration contract

The native session run-control patch remains capability-gated until validated against the pinned Mac mini Hermes build. External sessions and runs are observed through nonblocking plugin delivery plus reconciliation. Busy ownership is authoritative; the bridge queues instead of competing. Unsupported approval, interruption, attachment, job, model, or GUI actions are absent.

## Acceptance

- Active G2 state recovers after WSS loss, WebView recreation, bridge restart, and event compaction.
- Android restores cursor, sessions, pending attention, and notifications after process death and reboot.
- Exact-session routing survives simultaneous G2, Desktop, CLI, Telegram, job, and subagent activity.
- A forgotten or revoked device cannot reconnect.
- Attachments cannot cross sessions, exceed limits, persist indefinitely, or exhaust bridge memory.
- The physical EHPK shows the same detail pages and state transitions as the simulator.
