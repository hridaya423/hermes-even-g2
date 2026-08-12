package com.honey.hermesg2

import com.honey.hermesg2.data.ApprovalRequest
import org.junit.Assert.assertEquals
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

    @Test fun `native choice aliases map to explicit bridge actions`() {
        assertEquals("approveOnce", ApprovalPolicy.actionKindFor("approve_once"))
        assertEquals("approveSession", ApprovalPolicy.actionKindFor("session"))
        assertEquals("approveAlways", ApprovalPolicy.actionKindFor("approve-always"))
        assertEquals("deny", ApprovalPolicy.actionKindFor("reject"))
        assertEquals(null, ApprovalPolicy.actionKindFor("maybe"))
    }

    @Test fun `display labels are readable without changing native values`() {
        assertEquals("Approve once", ApprovalPolicy.displayLabel("approve_once"))
        assertEquals("Deny", ApprovalPolicy.displayLabel("deny"))
        assertEquals("Custom choice", ApprovalPolicy.displayLabel("custom_choice"))
    }

    @Test fun `the complete Hermes choice matrix never turns deny or cancel into approval`() {
        val expected = mapOf(
            "once" to "approveOnce",
            "session" to "approveSession",
            "always" to "approveAlways",
            "deny" to "deny",
            "reject" to "deny",
        )
        expected.forEach { (choice, action) -> assertEquals(action, ApprovalPolicy.actionKindFor(choice)) }
        assertEquals(null, ApprovalPolicy.actionKindFor("cancel"))
        assertEquals(null, ApprovalPolicy.actionKindFor("allow"))
    }

    @Test fun `every critical approval requires deliberate confirmation`() {
        val choices = listOf("once", "session", "always", "deny")
        choices.forEach { choice ->
            assertTrue("destructive $choice", ApprovalPolicy.requiresSecondConfirmation(choice, request(destructive = true)))
            assertTrue("sensitive $choice", ApprovalPolicy.requiresSecondConfirmation(choice, request(sensitive = true)))
        }
    }
}
