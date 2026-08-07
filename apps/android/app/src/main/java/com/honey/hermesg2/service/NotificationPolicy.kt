package com.honey.hermesg2.service

import com.honey.hermesg2.data.DurableEvent
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

data class AttentionNotification(val title: String)

object NotificationPolicy {
    fun forEvent(event: DurableEvent): AttentionNotification? {
        val initiatedByG2 = event.payload
            ?.jsonObject
            ?.get("initiatedByG2")
            ?.jsonPrimitive
            ?.booleanOrNull == true

        val title = when (event.kind) {
            "approval.required" -> "Hermes needs approval"
            "attention.created" -> "Hermes needs input"
            "run.failed" -> "Hermes run failed"
            "run.completed" -> if (initiatedByG2) "Hermes finished" else return null
            else -> return null
        }
        return AttentionNotification(title)
    }
}
