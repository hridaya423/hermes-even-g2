package com.honey.hermesg2.service

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.content.ContextCompat
import com.honey.hermesg2.data.SecureCredentials

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (SecureCredentials(context).load() != null) ContextCompat.startForegroundService(context, Intent(context, HermesConnectionService::class.java))
    }
}

