package com.honey.hermesg2.data

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class HermesStateReducerTest {
    private val json = Json { ignoreUnknownKeys = true }

    @Test fun `event is durable before cursor advances and duplicate replay is idempotent`() {
        val event = event("event-1", 41, "attention.created")
        val first = HermesStateReducer.apply(HermesPersistedState(), event)
        val duplicate = HermesStateReducer.apply(first, event)

        assertEquals(41, first.lastAckedCursor)
        assertEquals(listOf("event-1"), first.events.map { it.eventId })
        assertEquals(listOf("event-1"), first.pendingEvents.map { it.eventId })
        assertEquals(first, duplicate)
    }

    @Test fun `resolved approval removes only the matching request`() {
        val first = event("event-1", 1, "approval.required", "request-a")
        val second = event("event-2", 2, "approval.required", "request-b")
        val resolved = event("event-3", 3, "approval.resolved", "request-a")
        val state = HermesStateReducer.apply(
            HermesStateReducer.apply(HermesStateReducer.apply(HermesPersistedState(), first), second),
            resolved,
        )

        assertEquals(listOf("event-2"), state.pendingEvents.map { it.eventId })
        assertTrue(state.lastAckedCursor == 3L)
    }

    @Test fun `event history is bounded for process death recovery`() {
        val state = (1L..400L).fold(HermesPersistedState()) { current, cursor ->
            HermesStateReducer.apply(current, event("event-$cursor", cursor, "run.progress"))
        }

        assertEquals(256, state.events.size)
        assertEquals(145L, state.events.first().cursor)
        assertEquals(400L, state.events.last().cursor)
    }

    @Test fun `resnapshot preserves unresolved attention while pruning resolved approvals`() {
        val attention = event("attention", 1, "attention.created")
        val approval = event("approval", 2, "approval.required", "request-a")
        val state = HermesStateReducer.apply(
            HermesStateReducer.apply(HermesPersistedState(), attention),
            approval,
        )
        val snapshot = Snapshot(
            cursor = 2,
            pendingApprovals = listOf(
                ApprovalRequest("request-a", "session-1", "run-1", "shell"),
            ),
        )
        val preserved = HermesStateReducer.applySnapshot(state, snapshot)
        assertEquals(listOf("attention", "approval"), preserved.pendingEvents.map { it.eventId })

        val resolvedSnapshot = snapshot.copy(pendingApprovals = emptyList())
        val pruned = HermesStateReducer.applySnapshot(state, resolvedSnapshot)
        assertEquals(listOf("attention"), pruned.pendingEvents.map { it.eventId })
    }

    private fun event(id: String, cursor: Long, kind: String, requestId: String? = null): DurableEvent = DurableEvent(
        eventId = id,
        cursor = cursor,
        kind = kind,
        timestamp = "2026-08-12T00:00:00Z",
        source = "test",
        sessionId = "session-1",
        runId = "run-1",
        payload = requestId?.let { buildJsonObject { put("requestId", it) } },
    )
}
