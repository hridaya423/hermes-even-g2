package com.honey.hermesg2.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class BridgeWireFrameTest {
    @Test fun `replay gap is a control frame and never a durable event`() {
        val frame = BridgeWireFrameParser.parse(
            """{"type":"replay.gap","cursor":4,"oldestCursor":12,"latestCursor":18,"requiresSnapshot":true}""",
        )

        assertEquals(BridgeWireFrame.ReplayGap, frame)
    }

    @Test fun `sse keepalive is ignored without advancing state`() {
        assertEquals(BridgeWireFrame.Keepalive, BridgeWireFrameParser.parse("""{"type":"keepalive","cursor":8}"""))
    }

    @Test fun `normal durable events retain exact identity`() {
        val frame = BridgeWireFrameParser.parse(
            """{"eventId":"event-9","cursor":9,"kind":"run.completed","timestamp":"2026-08-12T00:00:00Z","source":"bridge","sessionId":"session-1"}""",
        )

        assertTrue(frame is BridgeWireFrame.Event)
        assertEquals("event-9", (frame as BridgeWireFrame.Event).value.eventId)
        assertEquals(9, frame.value.cursor)
    }
}
