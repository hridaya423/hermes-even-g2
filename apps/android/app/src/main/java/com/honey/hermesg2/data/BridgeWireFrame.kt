package com.honey.hermesg2.data

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

sealed interface BridgeWireFrame {
    data class Event(val value: DurableEvent) : BridgeWireFrame
    data object ReplayGap : BridgeWireFrame
    data object Keepalive : BridgeWireFrame
}

/** Parses both durable events and the bridge's non-event control envelopes. */
object BridgeWireFrameParser {
    private val json = Json { ignoreUnknownKeys = true }

    fun parse(text: String): BridgeWireFrame? {
        val objectValue = runCatching { json.parseToJsonElement(text).jsonObject }.getOrNull() ?: return null
        val type = objectValue["type"]?.jsonPrimitive?.contentOrNull
        val isGap = type == "replay.gap" || objectValue["gap"]?.jsonPrimitive?.booleanOrNull == true ||
            objectValue["resnapshotRequired"]?.jsonPrimitive?.booleanOrNull == true ||
            objectValue["requiresSnapshot"]?.jsonPrimitive?.booleanOrNull == true
        if (isGap) return BridgeWireFrame.ReplayGap
        if (type == "keepalive") return BridgeWireFrame.Keepalive
        return runCatching {
            BridgeWireFrame.Event(json.decodeFromString(DurableEvent.serializer(), text))
        }.getOrNull()
    }
}
