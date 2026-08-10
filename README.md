# Hermes G2

Hermes G2 is a private glasses-native client for a Hermes Agent instance running on a Mac mini. The bridge is the only service exposed over Tailscale; Hermes remains bound to loopback and its master API key never reaches Android or the Even Hub app.

## Layout

- `services/bridge`: FastAPI bridge, durable cursor log, pairing, audit, Hermes native-session client, local STT and live transports.
- `packages/hermes-plugin`: fail-open lifecycle observer installed beside Hermes.
- `packages/protocol`: versioned JSON Schema and generated-language source models.
- `apps/hub`: 576×288 Even Hub interface and `.ehpk` packaging.
- `apps/android`: full Compose fallback controller and persistent event service.
- `patches`: capability-gated Hermes native session-run control patch.
- `deploy`: launchd and Tailscale deployment assets for the Mac mini.

The repo intentionally has no runtime dependency on Hermes Mini.

## Local bridge

```sh
cd services/bridge
uv sync --extra dev
uv run hermes-g2-bridge migrate
uv run hermes-g2-bridge serve
```

The bridge refuses mutating session operations unless Hermes advertises native sessions, streaming, and session history. Approval and stop actions are separately hidden unless `session_run_control` and `session_approval_response` are advertised.

Android attachments use `POST /v1/attachments`. The bridge stages each upload under a private opaque filename and binds it to the authenticated device and exact native session before it can be claimed by one prompt. Current Hermes supports inline image parts but no uploaded-file API, so non-image files are exposed only as Mac-mini-local paths to Hermes's own tools.

## State recovery

Create a credential-free state backup while the bridge is running:

```sh
hermes-g2-bridge backup --output /secure/offline/hermes-g2-state.db
```

Restore only after stopping the bridge service, then start it again:

```sh
hermes-g2-bridge restore --input /secure/offline/hermes-g2-state.db --confirm
```

Backups contain durable events, session projections, run correlation, audit metadata and summary cache, but deliberately exclude device credentials, pairing codes, idempotency bodies and staged attachments. Restore preserves the credentials already installed on the target Mac mini and rejects any source containing credential rows.

## Security defaults

The bridge binds to `127.0.0.1`, accepts only hashed per-device bearer credentials, enforces scopes and idempotency, and redacts audit metadata. Put Tailscale Serve in front of the bridge; never bind the raw Hermes API to a tailnet address.
