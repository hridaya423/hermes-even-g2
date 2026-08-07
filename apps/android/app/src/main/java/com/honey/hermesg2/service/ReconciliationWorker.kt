package com.honey.hermesg2.service

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.honey.hermesg2.data.BridgeClient
import com.honey.hermesg2.data.SecureCredentials

class ReconciliationWorker(context: Context, parameters: WorkerParameters) :
    CoroutineWorker(context, parameters) {
    override suspend fun doWork(): Result {
        val credentials = SecureCredentials(applicationContext).load() ?: return Result.success()
        return runCatching { BridgeClient(credentials).snapshot() }
            .fold(onSuccess = { Result.success() }, onFailure = { Result.retry() })
    }
}
