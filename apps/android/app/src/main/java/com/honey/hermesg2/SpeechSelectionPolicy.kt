package com.honey.hermesg2

import com.honey.hermesg2.data.AgentMessage

object SpeechSelectionPolicy {
    /** Pick the newest complete assistant answer, never a tool or partial user message. */
    fun latestAssistant(messages: List<AgentMessage>): AgentMessage? =
        messages.firstOrNull { it.role == "assistant" && it.content.isNotBlank() }
}
