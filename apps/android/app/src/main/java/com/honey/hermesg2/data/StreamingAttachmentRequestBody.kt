package com.honey.hermesg2.data

import okio.BufferedSink
import okhttp3.MediaType
import okhttp3.RequestBody
import okhttp3.MediaType.Companion.toMediaType
import java.io.IOException

/**
 * Writes a content provider stream directly to OkHttp in 32 KiB chunks.  It
 * deliberately does not buffer or hash the file: the bridge computes the
 * digest while persisting it, and OkHttp cancellation propagates through the
 * sink to the provider stream.
 */
internal class StreamingAttachmentRequestBody(
    private val source: AttachmentSource,
    private val maxBytes: Long = AttachmentUploadPolicy.MAX_BYTES,
) : RequestBody() {
    override fun contentType(): MediaType = source.mediaType.toMediaType()

    override fun contentLength(): Long = source.declaredSize ?: -1L

    override fun writeTo(sink: BufferedSink) {
        var total = 0L
        source.openStream().use { input ->
            val buffer = ByteArray(BUFFER_SIZE)
            while (true) {
                val count = input.read(buffer)
                if (count < 0) break
                if (count == 0) continue
                total += count
                if (total > maxBytes) {
                    throw AttachmentTooLargeException(
                        "Attachment exceeds the ${AttachmentUploadPolicy.humanSize(maxBytes)} limit",
                    )
                }
                sink.write(buffer, 0, count)
            }
        }
        if (total == 0L) throw IOException("Attachment is empty")
        source.declaredSize?.let { expected ->
            if (total != expected) {
                throw IOException("Attachment changed while it was being uploaded")
            }
        }
    }

    private companion object {
        const val BUFFER_SIZE = 32 * 1024
    }
}

internal class AttachmentTooLargeException(message: String) : IOException(message)
