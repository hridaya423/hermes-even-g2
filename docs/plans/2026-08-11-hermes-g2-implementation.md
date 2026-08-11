# Hermes G2 Ultimate Implementation Plan

## 1. Hub transport and durable client state

Files: new `apps/hub/src/transport.ts`, refactor `api.ts`, `state.ts`, `App.tsx`, and Hub tests.

- Implement snapshot-first boot and reject actions until reconciliation completes.
- Persist cursor, selection, reading position, transcript destination, and idempotency keys.
- Add reconnect with jitter, cursor replay, acknowledgement, gap detection, and resnapshot.
- Add an idempotent outbox with visible retry/uncertain/stale states.
- Make forgetting a device call revocation before clearing local material.

## 2. Glasses rendering and interaction polish

- Refactor home and detail rendering around the explicit state machine in `DESIGN.md`.
- Render answer, tools, files/validation, subagents/jobs, and provenance as semantic pages on the physical display.
- Add small raster state marks, progress strips, meaningful run age, queue state, and recovery copy.
- Use targeted text upgrades for progress and avoid full-page flicker.
- Add simulator fixtures for every state, long text, audio flow, stale decisions, and reconnect.

## 3. Bridge delivery and attachment hardening

Files: bridge `app.py`, `store.py`, `service.py`, configuration, migrations, and tests.

- Return event retention bounds and explicit replay-gap responses.
- Add pairing attempt limits, device revocation UX support, scoped event filtering, and redaction.
- Validate attachment session targets; enforce streaming size, device/aggregate quota, TTL, and garbage collection.
- Remove consumed files and prevent whole-image memory amplification.
- Correlate external runs and authoritative busy ownership.

## 4. Plugin and Hermes compatibility

- Move plugin delivery off the hook path with a bounded fail-open queue.
- Emit external run lifecycle and stable session/run correlation.
- Fix deployment audit hook-name checks.
- Validate the native run-control patch against the pinned Hermes revision and make release verification mandatory when approval/stop capabilities are advertised.

## 5. Android repository and recovery

- Add a durable repository shared by the connection service, WorkManager, and Compose UI.
- Commit credentials synchronously before service startup.
- Persist snapshots and replayed events; expose live state to the UI.
- Bound polling delay, reconnect after Doze/network/process death/reboot, and preserve notification de-duplication.
- Add tests for pairing races, cursor persistence, replay, process recreation, notification redaction, and exact deep links.

## 6. Release proof

- Run bridge/protocol/Hub/Android/plugin tests and reproducible packaging.
- Validate simulator screenshots and SDK container constraints.
- Exercise physical G2 audio, approval, continuation, answer paging, reconnect, and interruption.
- Power-cycle Mac mini and Android, leave the laptop off, and complete a cellular/Tailscale G2 session before release.
