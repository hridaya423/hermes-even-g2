# Hermes G2 display system

The display is 576×288, black with the Even display's sixteen green luminance levels. It uses one persistent conversation surface and one paged detail surface. Hierarchy is positional rather than decorative: identity and connection in the header, the most urgent state in the body, and routing/run facts in the footer.

Typography uses a condensed system monospace for chrome and a highly readable system sans for responses. Controls have no rounded cards or ornamental icons. Brightness carries state: 100% for actionable text, 72% for content, 45% for metadata, and 24% for rules.

The default body's priority is approval, failure, meaningful active checkpoint, then final response. Raw deltas never displace these states. The selected session is captured when recording begins and cannot change until the transcript is sent or discarded.

Approval choices are rendered exactly as Hermes returns them. Persistent or sensitive choices add a second confirmation page. Speech can create a continuation but cannot choose a permission.

## Shared G2 control grammar

Hermes and Even Agent Control share gesture and safety semantics without sharing a visual shell. Swipe changes the visible session on the home surface and the current page or choice inside a mode. Press records, chooses, or confirms. Double press enters or leaves detail. The provider/session destination is frozen before recording or deciding begins.

Hermes is the conversational member of the pair. The response or active thought remains visually dominant; model, memory, tool, job, subagent, execution, and connection facts form a quiet information rail. Small raster state marks and progress strips may improve recognition, but they never become decorative dashboard furniture.

## Home surface

The header shows Hermes, session title and source, shortened ID, model, and connection/readiness. The body resolves to exactly one priority state: approval, failure/interruption, active tool or subagent checkpoint, queued continuation, or latest answer. The footer shows session position, pending count, run age, and core/GUI readiness.

Pinned sessions lead the carousel, then recently active Desktop, CLI, Telegram, job, and G2 sessions. A `NEW G2 SESSION` row creates a native session. Background activity can raise urgency but cannot silently change the selected session.

## Detail surface

Detail pages are a compact work record: complete answer with reading position, tool timeline and results, files/attachments and validation, subagent tree, job state, and session provenance/readiness. Pages are capability-aware and absent when empty. Long text breaks at semantic boundaries.

## Recovery behavior

Pairing, synchronization, recording, transcription, review, approval, critical confirmation, busy, queued, reconnecting, replay gap, stale action, provider unavailable, GUI unavailable, and offline are explicit states. Startup reconciles a fresh snapshot before accepting actions. Reconnect resumes from the persisted cursor or resnapshots when history has compacted. No interrupted task reruns automatically.
