package com.honey.hermesg2

object AttachmentSelectionPolicy {
    const val MAX_ATTACHMENTS = 10
    const val MAX_BYTES = 25L * 1024L * 1024L

    fun availableSlots(existingCount: Int): Int =
        (MAX_ATTACHMENTS - existingCount).coerceAtLeast(0)

    fun acceptedCount(existingCount: Int, selectedCount: Int): Int =
        selectedCount.coerceAtMost(availableSlots(existingCount))

    fun humanSize(bytes: Long): String = when {
        bytes >= 1024L * 1024L -> "%.1f MB".format(bytes / (1024.0 * 1024.0))
        bytes >= 1024L -> "%.0f KB".format(bytes / 1024.0)
        else -> "$bytes B"
    }
}
