package com.honey.hermesg2

import com.honey.hermesg2.data.ActiveRun
import com.honey.hermesg2.data.AgentAction
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

class RunControlPolicyTest {
    private fun policy(): Pair<Any, Class<*>> {
        val type = runCatching { Class.forName("com.honey.hermesg2.RunControlPolicy") }.getOrNull()
        assertNotNull("RunControlPolicy must exist", type)
        return type!!.getField("INSTANCE").get(null) to type
    }

    @Test fun `active run selection is exact to the visible session`() {
        val runs = listOf(
            ActiveRun("run-a", "session-a", status = "started", updatedAt = "now"),
            ActiveRun("run-b", "session-b", status = "started", updatedAt = "now"),
        )
        val (instance, type) = policy()
        val method = type.getMethod("activeRunFor", String::class.java, List::class.java)

        assertEquals(runs[0], method.invoke(instance, "session-a", runs))
        assertNull(method.invoke(instance, "session-c", runs))
    }

    @Test fun `stop action preserves exact session and run destination`() {
        val run = ActiveRun("run-a", "session-a", status = "started", updatedAt = "now")
        val (instance, type) = policy()
        val method = type.getMethod(
            "stopAction",
            String::class.java,
            ActiveRun::class.java,
            String::class.java,
            String::class.java,
        )

        val action = method.invoke(instance, "device-a", run, "idempotency-a", "2026-08-09T22:00:00Z") as AgentAction

        assertEquals("stopRun", action.kind)
        assertEquals("session-a", action.sessionId)
        assertEquals("run-a", action.runId)
        assertEquals("running", action.expectedState)
    }
}
