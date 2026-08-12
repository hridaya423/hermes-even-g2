package com.honey.hermesg2

import com.honey.hermesg2.data.ApprovalRequest

/** Maps only the approval choices Hermes explicitly offers to safe bridge actions. */
object ApprovalPolicy {
    private val choiceAliases = mapOf(
        "once" to "approveOnce",
        "approve_once" to "approveOnce",
        "approve-once" to "approveOnce",
        "session" to "approveSession",
        "approve_session" to "approveSession",
        "approve-session" to "approveSession",
        "always" to "approveAlways",
        "approve_always" to "approveAlways",
        "approve-always" to "approveAlways",
        "deny" to "deny",
        "reject" to "deny",
    )

    fun actionKindFor(choice: String): String? =
        choiceAliases[choice.trim().lowercase()]

    fun displayLabel(choice: String): String = when (choice.trim().lowercase()) {
        "once", "approve_once", "approve-once" -> "Approve once"
        "session", "approve_session", "approve-session" -> "Approve session"
        "always", "approve_always", "approve-always" -> "Always approve"
        "deny", "reject" -> "Deny"
        else -> choice.replace('_', ' ').replace('-', ' ').trim()
            .replaceFirstChar { if (it.isLowerCase()) it.titlecase() else it.toString() }
    }

    fun requiresSecondConfirmation(choice: String, request: ApprovalRequest): Boolean =
        choice.trim().lowercase() in setOf("session", "approve_session", "approve-session", "always", "approve_always", "approve-always") ||
            request.destructive || request.sensitive
}
