package com.honey.hermesg2

import com.honey.hermesg2.data.RuntimeReadiness
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ReadinessPolicyTest {
    @Test fun `diagnostics expose each private dependency and recovery hint`() {
        val lines = ReadinessPolicy.lines(RuntimeReadiness(coreReady = false, tailscale = false, guiReady = false, stt = true), "Bridge timed out")
        assertEquals(8, lines.size)
        assertTrue(lines.any { it.label == "Tailscale" && !it.ready })
        assertTrue(lines.any { it.label == "GUI tools" && it.detail.contains("logged out") })
        assertTrue(lines.any { it.label == "Last error" && it.detail == "Bridge timed out" })
        assertEquals("Action needs attention", ReadinessPolicy.headline(RuntimeReadiness(), "Bridge timed out"))
    }
}
