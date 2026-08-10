package com.honey.hermesg2.data

import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class BridgeClientTest {
    @Test fun `upload sends private multipart attachment to the exact session`() = runBlocking {
        val server = MockWebServer()
        server.enqueue(MockResponse().setHeader("Content-Type", "application/json").setBody(
            """{"attachmentId":"attachment-1","sessionId":"session-1","name":"notes.pdf","mediaType":"application/pdf","size":8,"sha256":"digest"}"""
        ))
        server.start()
        try {
            val client = BridgeClient(DeviceCredentials(server.url("/").toString().trimEnd('/'), "device-1", "credential-1"))

            val uploaded = client.uploadAttachment("session-1", "notes.pdf", "application/pdf", "document".encodeToByteArray())

            assertEquals("attachment-1", uploaded.attachmentId)
            assertEquals("session-1", uploaded.sessionId)
            val request = server.takeRequest()
            assertEquals("/v1/attachments?sessionId=session-1", request.path)
            assertEquals("Bearer credential-1", request.getHeader("Authorization"))
            val body = request.body.readUtf8()
            assertTrue(body.contains("filename=\"notes.pdf\""))
            assertTrue(body.contains("Content-Type: application/pdf"))
            assertTrue(body.contains("document"))
        } finally {
            server.shutdown()
        }
    }
}
