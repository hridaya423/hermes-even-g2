package com.honey.hermesg2.service

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.honey.hermesg2.data.BridgeClient
import com.honey.hermesg2.data.HermesStateRepository
import com.honey.hermesg2.data.SecureCredentials

class ReconciliationWorker(context: Context, parameters: WorkerParameters) :
    CoroutineWorker(context, parameters) {
    override suspend fun doWork(): Result {
        val credentials = SecureCredentials(applicationContext).load() ?: return Result.success()
        val repository = HermesStateRepository.get(applicationContext)
        return runCatching { BridgeClient(credentials).snapshot() }
            .fold(
                onSuccess = { snapshot ->
                    // Persist the complete authoritative snapshot, not only its
                    // cursor; this is what makes a killed service recoverable.
                    repository.persistSnapshot(snapshot)
                    Result.success()
                },
                onFailure = { Result.retry() },
            )
    }
}
