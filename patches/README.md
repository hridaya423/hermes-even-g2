# Hermes compatibility patch

`hermes-session-run-control.patch` targets the API server layout in Hermes 0.20/current upstream. Apply it only to the pinned Mac-mini checkout, run the upstream API-server suite, and keep the bridge controls disabled until `/v1/capabilities` returns both `session_run_control` and `session_approval_response`.

The patch deliberately routes approval and stop through Hermes's existing Runs API registries. The bridge never reaches into Hermes memory or reconstructs history.

