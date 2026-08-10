package com.honey.hermesg2

import com.honey.hermesg2.data.AgentAction
import com.honey.hermesg2.data.AttachmentUpload

object AttachmentPromptPolicy {
    fun promptAction(
        deviceId: String,
        sessionId: String,
        sessionBusy: Boolean,
        text: String,
        attachments: List<AttachmentUpload>,
        idempotencyKey: String,
        createdAt: String,
    ): AgentAction {
        require(attachments.all { it.sessionId == sessionId }) {
            "Every attachment must belong to the visible session"
        }
        return AgentAction(
            kind = if (sessionBusy) "queuePrompt" else "prompt",
            deviceId = deviceId,
            idempotencyKey = idempotencyKey,
            sessionId = sessionId,
            createdAt = createdAt,
            payload = buildMap {
                put("text", text)
                if (attachments.isNotEmpty()) {
                    put("attachmentIds", attachments.joinToString(",") { it.attachmentId })
                }
            },
        )
    }
}
