package com.honey.hermesg2.data

import android.content.ContentResolver
import android.net.Uri
import android.provider.OpenableColumns
import java.io.ByteArrayInputStream
import java.io.IOException
import java.io.InputStream
import java.util.Locale

/**
 * A replayable attachment source.  The bridge request body may be retried, so
 * [openStream] must return a fresh stream for every invocation.
 */
interface AttachmentSource {
    val sessionId: String
    val name: String
    val mediaType: String
    val declaredSize: Long?

    fun openStream(): InputStream
}

/**
 * Metadata-only view over a content URI.  The bytes stay owned by the
 * provider and are read once, in bounded chunks, by OkHttp when it writes the
 * multipart request.
 */
class ContentResolverAttachmentSource(
    private val resolver: ContentResolver,
    private val uri: Uri,
    override val sessionId: String,
) : AttachmentSource {
    private val metadata = resolver.query(
        uri,
        arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE),
        null,
        null,
        null,
    )?.use { cursor ->
        val nameIndex = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
        val sizeIndex = cursor.getColumnIndex(OpenableColumns.SIZE)
        if (!cursor.moveToFirst()) {
            null
        } else {
            val displayName = nameIndex.takeIf { it >= 0 }?.let { cursor.getString(it) }
            val size = sizeIndex.takeIf { it >= 0 && !cursor.isNull(it) }?.let { cursor.getLong(it) }
            displayName to size
        }
    }

    override val name: String = AttachmentMetadata.sanitizeName(metadata?.first ?: "attachment")
    override val mediaType: String = AttachmentMetadata.normalizeMediaType(resolver.getType(uri))
    override val declaredSize: Long? = metadata?.second?.takeIf { it >= 0L }

    init {
        AttachmentMetadata.validateSession(sessionId)
        declaredSize?.let { AttachmentMetadata.validateDeclaredSize(it) }
    }

    override fun openStream(): InputStream = resolver.openInputStream(uri)
        ?: throw IOException("Unable to open attachment '$name'")
}

/** Small in-memory adapter retained for tests and callers that already own bytes. */
class ByteArrayAttachmentSource(
    override val sessionId: String,
    name: String,
    mediaType: String,
    private val bytes: ByteArray,
) : AttachmentSource {
    override val name: String = AttachmentMetadata.sanitizeName(name)
    override val mediaType: String = AttachmentMetadata.normalizeMediaType(mediaType)
    override val declaredSize: Long = bytes.size.toLong()

    init {
        AttachmentMetadata.validateSession(sessionId)
        AttachmentMetadata.validateDeclaredSize(declaredSize)
        require(bytes.isNotEmpty()) { "Attachment is empty" }
    }

    override fun openStream(): InputStream = ByteArrayInputStream(bytes)
}

internal object AttachmentMetadata {
    private const val MAX_NAME_LENGTH = 255
    private const val MAX_MEDIA_TYPE_LENGTH = 128
    private val mediaTypePattern = Regex("^[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+$")

    fun validateSession(sessionId: String) {
        require(sessionId.isNotBlank() && sessionId.length <= 256) { "Attachment session is invalid" }
        require(sessionId.none { it == '\u0000' || it == '\r' || it == '\n' }) {
            "Attachment session is invalid"
        }
    }

    fun validateDeclaredSize(size: Long) {
        require(size > 0L) { "Attachment is empty" }
        require(size <= AttachmentUploadPolicy.MAX_BYTES) {
            "Attachment exceeds the ${AttachmentUploadPolicy.humanSize(AttachmentUploadPolicy.MAX_BYTES)} limit"
        }
    }

    fun sanitizeName(value: String): String {
        val candidate = value.substringAfterLast('/').substringAfterLast('\\')
            .filterNot { it == '\u0000' || it == '\r' || it == '\n' || it.code < 0x20 || it == '\u007f' }
            .trim()
        require(candidate.isNotEmpty() && candidate !in setOf(".", "..")) { "Attachment filename is invalid" }
        require(candidate.length <= MAX_NAME_LENGTH) { "Attachment filename is too long" }
        return candidate
    }

    fun normalizeMediaType(value: String?): String {
        val mediaType = value?.trim()?.lowercase(Locale.US).orEmpty().ifBlank { "application/octet-stream" }
        require(mediaType.length <= MAX_MEDIA_TYPE_LENGTH && mediaTypePattern.matches(mediaType)) {
            "Attachment MIME type is invalid"
        }
        return mediaType
    }
}

internal object AttachmentUploadPolicy {
    const val MAX_BYTES = 25L * 1024L * 1024L

    fun humanSize(bytes: Long): String = when {
        bytes >= 1024L * 1024L -> "%.1f MB".format(Locale.US, bytes / (1024.0 * 1024.0))
        bytes >= 1024L -> "%.0f KB".format(Locale.US, bytes / 1024.0)
        else -> "$bytes B"
    }
}
