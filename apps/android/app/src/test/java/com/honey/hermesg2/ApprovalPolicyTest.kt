package com.honey.hermesg2

import com.honey.hermesg2.data.ApprovalRequest
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ApprovalPolicyTest {
    private fun request(destructive: Boolean = false, sensitive: Boolean = false) = ApprovalRequest(
        requestId = "request",
        sessionId = "session",
        runId = "run",
        tool = "shell",
        destructive = destructive,
        sensitive = sensitive,
    )

    @Test fun `persistent choices always require a second confirmation`() {
        assertTrue(ApprovalPolicy.requiresSecondConfirmation("session", request()))
        assertTrue(ApprovalPolicy.requiresSecondConfirmation("always", request()))
    }

    @Test fun `sensitive and destructive requests always require a second confirmation`() {
        assertTrue(ApprovalPolicy.requiresSecondConfirmation("once", request(destructive = true)))
        assertTrue(ApprovalPolicy.requiresSecondConfirmation("deny", request(sensitive = true)))
    }

    @Test fun `ordinary one-time and deny decisions require one confirmation`() {
        assertFalse(ApprovalPolicy.requiresSecondConfirmation("once", request()))
        assertFalse(ApprovalPolicy.requiresSecondConfirmation("deny", request()))
    }
}
