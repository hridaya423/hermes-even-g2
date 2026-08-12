package com.honey.hermesg2

import com.honey.hermesg2.data.AgentMessage
import org.junit.Assert.assertEquals
import org.junit.Test

class SpeechSelectionPolicyTest {
    @Test fun `selects newest complete assistant answer from newest-first history`() {
        val selected = SpeechSelectionPolicy.latestAssistant(
            listOf(
                AgentMessage("a2", "s", "assistant", "Latest answer"),
                AgentMessage("tool", "s", "tool", "command output"),
                AgentMessage("a1", "s", "assistant", "Older answer"),
            ),
        )
        assertEquals("a2", selected?.id)
    }

    @Test fun `does not speak blank assistant placeholders`() {
        assertEquals(null, SpeechSelectionPolicy.latestAssistant(listOf(AgentMessage("a", "s", "assistant", ""))))
    }
}
