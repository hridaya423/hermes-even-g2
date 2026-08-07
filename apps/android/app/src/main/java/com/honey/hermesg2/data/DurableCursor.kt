package com.honey.hermesg2.data

import android.content.Context

class DurableCursor(context: Context) {
    private val preferences = context.getSharedPreferences("hermes_g2_delivery", Context.MODE_PRIVATE)

    fun load(): Long? = if (preferences.contains("acknowledged_cursor")) {
        preferences.getLong("acknowledged_cursor", 0)
    } else {
        null
    }

    fun persistBeforeAcknowledge(cursor: Long) {
        preferences.edit().putLong("acknowledged_cursor", cursor).commit()
    }
}
