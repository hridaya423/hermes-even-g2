# Mac mini deployment

The boot path uses LaunchDaemons because core Hermes chat must work without an Aqua login session. The bridge and Hermes bind only to loopback. Tailscale Serve terminates private HTTPS/WSS and forwards `/hermes-g2` to the bridge; never forward port 8642.

1. Pin and patch the Mac mini Hermes checkout, then run `tests/gateway/test_session_api.py`.
2. Set `API_SERVER_KEY` in Hermes's root-owned environment and install the Hermes LaunchDaemon.
3. Run `sudo scripts/install-mac-mini.sh`. When the verified user-tier deployment exists, it promotes that environment, SQLite device/event state, Whisper model and summary helper into root-owned locations without printing or rotating credentials. Without a staged deployment it creates `/etc/hermes-g2/bridge.env` with mode `0600` and stops for secret editing.
4. Install `whisper.cpp`, then run `scripts/install-whisper-model.sh`. The selected `tiny.en-q5_1` model transcribed the deployment fixture correctly in 0.58 seconds on the Mac mini; the script verifies the published SHA-256 before installation.
5. Run `sudo scripts/configure-tailscale.sh`, confirm the MagicDNS HTTPS origin, and generate separate `android`, `hub`, and `simulator` pairing codes.
6. Run `scripts/install-hermes-plugin.sh`. It stages the observer and its independent secret but deliberately does not restart Hermes; restart only after any in-progress Hermes repair is complete.
7. Run `hermes-g2-bridge doctor` without copying its secret environment into a shell history.
8. Run `scripts/audit-mac-mini.sh` after repairs or reboots. It is read-only and reports whether the Hermes patch is applied, the observer is active, core services are reachable, Tailscale is running, and the system LaunchDaemon is loaded; it never restarts Hermes or changes credentials.

Before a bridge upgrade, run `hermes-g2-bridge backup --output <offline-path>`. To restore, stop `system/com.honey.hermes-g2.bridge`, run `hermes-g2-bridge restore --input <offline-path> --confirm`, then bootstrap the service again. The restore transaction never replaces device credentials, and the command rejects backups that contain any credential rows.

GUI-dependent tools remain unavailable while the Mac mini is logged out. The bridge exposes this separately from core readiness; it does not restart or silently rerun a failed turn.

For pre-production testing without administrator access, run `scripts/stage-mac-mini-user.sh`. It creates an isolated bridge venv and credential file under `~/.hermes/hermes-g2`, installs only a user LaunchAgent, and leaves the Hermes checkout and Hermes Mini untouched. This tier stops at logout, so it cannot satisfy the boot-without-login acceptance test; migrate it with `install-mac-mini.sh` once an administrator is present.
