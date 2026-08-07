package com.honey.hermesg2.service

import com.honey.hermesg2.data.DurableEvent
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class NotificationPolicyTest {
    private fun event(kind: String, initiatedByG2: Boolean? = null) = DurableEvent(
        eventId = "event",
        cursor = 1,
        kind = kind,
        timestamp = "2026-08-07T00:00:00Z",
        source = "hermes",
        payload = initiatedByG2?.let { value -> buildJsonObject { put("initiatedByG2", value) } },
    )

    @Test fun `approval failure and input create redacted alerts`() {
        assertEquals("Hermes needs approval", NotificationPolicy.forEvent(event("approval.required"))?.title)
        assertEquals("Hermes run failed", NotificationPolicy.forEvent(event("run.failed"))?.title)
        assertEquals("Hermes needs input", NotificationPolicy.forEvent(event("attention.created"))?.title)
    }

    @Test fun `only G2 owned completions notify`() {
        assertNull(NotificationPolicy.forEvent(event("run.completed")))
        assertNull(NotificationPolicy.forEvent(event("run.completed", false)))
        assertEquals("Hermes finished", NotificationPolicy.forEvent(event("run.completed", true))?.title)
    }

    @Test fun `progress never creates notification`() {
        assertNull(NotificationPolicy.forEvent(event("run.progress")))
        assertNull(NotificationPolicy.forEvent(event("tool.completed")))
    }
}
