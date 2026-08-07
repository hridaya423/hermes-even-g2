package com.honey.hermesg2

import com.honey.hermesg2.data.ApprovalRequest

object ApprovalPolicy {
    fun requiresSecondConfirmation(choice: String, request: ApprovalRequest): Boolean =
        choice in setOf("session", "always") || request.destructive || request.sensitive
}
