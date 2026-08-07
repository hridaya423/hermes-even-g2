package com.honey.hermesg2

import android.app.Application
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import com.honey.hermesg2.service.ReconciliationWorker
import java.util.concurrent.TimeUnit

class HermesApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        val request = PeriodicWorkRequestBuilder<ReconciliationWorker>(15, TimeUnit.MINUTES)
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .build()
        WorkManager.getInstance(this).enqueueUniquePeriodicWork(
            "hermes-g2-reconciliation",
            ExistingPeriodicWorkPolicy.UPDATE,
            request,
        )
    }
}
