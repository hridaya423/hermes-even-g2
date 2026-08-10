# Hermes G2 specification coverage

This matrix separates repository work from deployment and physical acceptance. A feature is marked implemented only when its production path and automated contract tests exist.

## Implemented in this repository

- Private FastAPI bridge, scoped device pairing, hashed credentials, revocation, idempotent actions, durable cursor replay, audit metadata and event compaction.
- Exact native-session creation, continuation, fork, rename, model lock, busy-session queuing, message history and session discovery.
- Capability-gated native-session approvals and interruption, including all Hermes-offered choices and stale request rejection.
- Hermes observer plugin, reconciliation, local Whisper transcription, deterministic/Apple response condensation, LaunchDaemon assets, Tailscale Serve configuration and a read-only doctor/audit path.
- Dense G2 Hub client with session carousel, exact destination binding, dictation confirmation, progress/final response detail, approvals and stop confirmation.
- Android controller with full history, sessions, jobs, models, skills/toolsets, device revocation, audit, manual TTS, WSS/SSE/poll recovery, Doze reconciliation and boot restart.
- Android attachment upload through a private bridge staging area. Attachments are opaque-ID, device and session bound; images become supported Hermes inline image parts, while documents remain host-local paths available to Hermes tools. This adapts the current upstream limitation that the API server accepts inline images but rejects uploaded files.
- Reproducible APK/EHPK builds and one release gate covering Python, TypeScript, Kotlin, Swift, shell deployment fixtures and packaged artifacts.

## Code work still required after the recovery pass

- Validate the maintained native-session run-control patch against the pinned Mac-mini Hermes checkout and current upstream; refresh the patch if either layout has drifted.
- Repeat this matrix audit after those changes and close any newly demonstrated gap.

## External acceptance, not representable as repository-only code

- Apply the compatibility patch to the pinned Hermes checkout and submit it upstream.
- Install root-owned LaunchDaemons and Tailscale Serve using administrator authorization.
- Pair physical Android, Hub and simulator devices with newly generated credentials, then revoke development credentials.
- Run real G2 microphone/gesture acceptance, Android Doze/process-death/reboot tests, and Mac-mini logged-out/power-cycle tests.

The upstream API behavior referenced by the attachment design is documented in the official [Hermes API server guide](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server/).
