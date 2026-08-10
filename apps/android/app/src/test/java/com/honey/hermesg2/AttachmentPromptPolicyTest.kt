package com.honey.hermesg2

import com.honey.hermesg2.data.AttachmentUpload
import org.junit.Assert.assertEquals
import org.junit.Test

class AttachmentPromptPolicyTest {
    @Test fun `prompt action binds uploaded IDs to the visible session`() {
        val attachments = listOf(
            AttachmentUpload("attachment-1", "session-1", "a.png", "image/png", 10, "a"),
            AttachmentUpload("attachment-2", "session-1", "b.pdf", "application/pdf", 20, "b"),
        )

        val action = AttachmentPromptPolicy.promptAction(
            deviceId = "device-1",
            sessionId = "session-1",
            sessionBusy = true,
            text = "Inspect these",
            attachments = attachments,
            idempotencyKey = "idempotency-1",
            createdAt = "2026-08-10T00:00:00Z",
        )

        assertEquals("queuePrompt", action.kind)
        assertEquals("session-1", action.sessionId)
        assertEquals("Inspect these", action.payload["text"])
        assertEquals("attachment-1,attachment-2", action.payload["attachmentIds"])
    }

    @Test(expected = IllegalArgumentException::class)
    fun `prompt action rejects an attachment uploaded for another session`() {
        AttachmentPromptPolicy.promptAction(
            "device-1",
            "session-1",
            false,
            "Inspect",
            listOf(AttachmentUpload("attachment-1", "session-2", "a.png", "image/png", 10, "a")),
            "idempotency-1",
            "2026-08-10T00:00:00Z",
        )
    }
}
