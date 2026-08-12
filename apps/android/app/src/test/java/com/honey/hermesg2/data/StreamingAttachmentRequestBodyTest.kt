package com.honey.hermesg2.data

import okio.Buffer
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.IOException
import java.io.InputStream
import java.io.InterruptedIOException

class StreamingAttachmentRequestBodyTest {
    @Test fun `writes source directly in a bounded replayable stream`() {
        val source = ByteArrayAttachmentSource("session-1", "notes.txt", "text/plain", "hello".encodeToByteArray())
        val body = StreamingAttachmentRequestBody(source)
        val first = Buffer().also(body::writeTo)
        val second = Buffer().also(body::writeTo)

        assertEquals(5L, body.contentLength())
        assertEquals("text/plain", body.contentType()?.toString())
        assertArrayEquals("hello".encodeToByteArray(), first.readByteArray())
        assertArrayEquals("hello".encodeToByteArray(), second.readByteArray())
    }

    @Test fun `rejects a stream that grows beyond the configured limit`() {
        val source = FakeSource(
            sessionId = "session-1",
            name = "large.bin",
            mediaType = "application/octet-stream",
            declaredSize = null,
            streamFactory = { ByteArrayInputStream(ByteArray(12) { 7 }) },
        )

        val failure = runCatching {
            StreamingAttachmentRequestBody(source, maxBytes = 8).writeTo(Buffer())
        }.exceptionOrNull()

        assertTrue(failure is AttachmentTooLargeException)
    }

    @Test fun `closes the provider stream when cancellation interrupts the write`() {
        val input = object : InputStream() {
            var closed = false
            override fun read(): Int = throw InterruptedIOException("cancelled")
            override fun close() { closed = true }
        }
        val source = FakeSource(
            sessionId = "session-1",
            name = "cancel.bin",
            mediaType = "application/octet-stream",
            declaredSize = null,
            streamFactory = { input },
        )

        val failure = runCatching { StreamingAttachmentRequestBody(source).writeTo(Buffer()) }.exceptionOrNull()

        assertTrue(failure is InterruptedIOException)
        assertTrue(input.closed)
    }

    @Test fun `detects a provider changing the file after metadata was read`() {
        val source = FakeSource(
            sessionId = "session-1",
            name = "changed.txt",
            mediaType = "text/plain",
            declaredSize = 5,
            streamFactory = { ByteArrayInputStream("four".encodeToByteArray()) },
        )

        val failure = runCatching { StreamingAttachmentRequestBody(source).writeTo(Buffer()) }.exceptionOrNull()

        assertTrue(failure is IOException)
        assertEquals("Attachment changed while it was being uploaded", failure?.message)
    }

    @Test fun `rejects invalid mime and empty sources before network use`() {
        val invalidMime = runCatching {
            ByteArrayAttachmentSource("session-1", "notes.txt", "text/plain\r\nX-Leak: 1", byteArrayOf(1))
        }.exceptionOrNull()
        val empty = runCatching {
            ByteArrayAttachmentSource("session-1", "empty.txt", "text/plain", byteArrayOf())
        }.exceptionOrNull()

        assertTrue(invalidMime is IllegalArgumentException)
        assertTrue(empty is IllegalArgumentException)
    }

    private class FakeSource(
        override val sessionId: String,
        override val name: String,
        override val mediaType: String,
        override val declaredSize: Long?,
        private val streamFactory: () -> InputStream,
    ) : AttachmentSource {
        override fun openStream(): InputStream = streamFactory()
    }
}
