package com.honey.hermesg2.data

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Test

class BridgeModelsTest {
    private val json = Json { ignoreUnknownKeys = true }

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

    @Test fun `bridge device record decodes scopes and durable cursor`() {
        val devices = json.decodeFromString<List<DeviceRecord>>(
            """[{"id":"android-1","name":"Pixel","kind":"android","scopes":["sessions:read","devices:manage"],"created_at":"2026-08-08T00:00:00Z","expires_at":null,"revoked_at":null,"acknowledged_cursor":42,"scopes_json":"ignored"}]"""
        )
        assertEquals(listOf("sessions:read", "devices:manage"), devices.single().scopes)
        assertEquals(42, devices.single().acknowledgedCursor)
    }
}
