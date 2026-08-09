package com.honey.hermesg2

import com.honey.hermesg2.data.ActiveRun
import com.honey.hermesg2.data.AgentAction

object RunControlPolicy {
    fun activeRunFor(sessionId: String, runs: List<ActiveRun>): ActiveRun? =
        runs.firstOrNull { it.sessionId == sessionId }

    fun stopAction(
        deviceId: String,
        run: ActiveRun,
        idempotencyKey: String,
        createdAt: String,
    ) = AgentAction(
        kind = "stopRun",
        deviceId = deviceId,
        idempotencyKey = idempotencyKey,
        sessionId = run.sessionId,
        runId = run.runId,
        expectedState = "running",
        createdAt = createdAt,
    )
}
