package com.honey.hermesg2.data

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Test

class BridgeModelsTest {
    private val json = Json { ignoreUnknownKeys = true }

    @Test fun `pairing request declares the Android device kind`() {
        val encoded = json.encodeToString(PairingRequest.serializer(), PairingRequest("123456", "Pixel", deviceKind = "android"))
        assertEquals("android", json.parseToJsonElement(encoded).jsonObject["deviceKind"]?.jsonPrimitive?.content)
    }

    @Test fun `live Hermes job envelope decodes into controller fields`() {
        val value = json.decodeFromString<JobList>(
            """{"jobs":[{"id":"daily","name":"Daily review","state":"paused","enabled":false,"schedule_display":"Every day","next_run_at":"2026-08-09T08:00:00Z","last_status":"completed","prompt":"redacted from model"}]}"""
        )
        val job = value.jobs.single()
        assertEquals("daily", job.id)
        assertEquals("Every day", job.scheduleDisplay)
        assertEquals("2026-08-09T08:00:00Z", job.nextRunAt)
        assertEquals("completed", job.lastStatus)
    }

    @Test fun `snapshot preserves exact active run identity`() {
        val snapshot = json.decodeFromString<Snapshot>(
            """{"sessions":[],"activeRuns":[{"runId":"run-1","sessionId":"session-1","deviceId":"device-1","initiatedByG2":true,"status":"started","updatedAt":"2026-08-09T22:00:00Z"}]}"""
        )
        val encoded = json.encodeToJsonElement(Snapshot.serializer(), snapshot).jsonObject

        assertEquals(
            "run-1",
            encoded["activeRuns"]?.jsonArray?.single()?.jsonObject?.get("runId")?.jsonPrimitive?.content,
        )
        assertEquals(
            "session-1",
            encoded["activeRuns"]?.jsonArray?.single()?.jsonObject?.get("sessionId")?.jsonPrimitive?.content,
        )
    }

    @Test fun `bridge device record decodes scopes and durable cursor`() {
        val devices = json.decodeFromString<List<DeviceRecord>>(
            """[{"id":"android-1","name":"Pixel","kind":"android","scopes":["sessions:read","devices:manage"],"created_at":"2026-08-08T00:00:00Z","expires_at":null,"revoked_at":null,"acknowledged_cursor":42,"scopes_json":"ignored"}]"""
        )
        assertEquals(listOf("sessions:read", "devices:manage"), devices.single().scopes)
        assertEquals(42, devices.single().acknowledgedCursor)
    }

    @Test fun `live model options decode authenticated provider choices`() {
        val value = json.decodeFromString<ModelOptions>(
            """{"provider":"openai","model":"gpt-current","providers":[{"slug":"openai","name":"OpenAI","authenticated":true,"models":["gpt-current","gpt-next"],"auth_type":"api_key"}]}"""
        )
        assertEquals("openai", value.provider)
        assertEquals("gpt-current", value.model)
        assertEquals(listOf("gpt-current", "gpt-next"), value.providers.single().models)
    }
}
