package com.honey.hermesg2.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

@Serializable data class DeviceCredentials(val origin: String, val deviceId: String, val credential: String)
@Serializable data class PairingRequest(val code: String, val deviceName: String, val deviceKind: String)
@Serializable data class PairingResponse(val deviceId: String, val credential: String, val scopes: List<String>)
@Serializable data class SessionSummary(val id: String, val title: String = "Untitled", val source: String = "unknown", val model: String? = null, val state: String = "idle", val updatedAt: String = "", val pinned: Boolean = false, val executionReady: Boolean = true, val latestAnswer: String? = null)
@Serializable data class AgentMessage(val id: String, val sessionId: String, val role: String, val content: String, val reasoning: String? = null, val timestamp: String? = null, val finishReason: String? = null, val toolName: String? = null, val tokenCount: Int? = null)
@Serializable data class MessagePage(val data: List<AgentMessage> = emptyList(), val total: Int = 0, val hasMore: Boolean = false, val offset: Int = 0, val limit: Int = 100)
@Serializable data class AttachmentUpload(val attachmentId: String, val sessionId: String, val name: String, val mediaType: String, val size: Long, val sha256: String)
@Serializable data class ActiveRun(val runId: String, val sessionId: String, val deviceId: String? = null, val initiatedByG2: Boolean = false, val status: String, val updatedAt: String)
@Serializable data class JobSummary(val id: String, val name: String = "Untitled job", val state: String = "unknown", val enabled: Boolean = true, @SerialName("schedule_display") val scheduleDisplay: String? = null, @SerialName("next_run_at") val nextRunAt: String? = null, @SerialName("last_status") val lastStatus: String? = null, @SerialName("last_error") val lastError: String? = null)
@Serializable data class JobList(val jobs: List<JobSummary> = emptyList())
@Serializable data class DeviceRecord(val id: String, val name: String, val kind: String, val scopes: List<String> = emptyList(), @SerialName("created_at") val createdAt: String, @SerialName("expires_at") val expiresAt: String? = null, @SerialName("revoked_at") val revokedAt: String? = null, @SerialName("acknowledged_cursor") val acknowledgedCursor: Long = 0)
@Serializable data class ModelProvider(val slug: String, val name: String = slug, val authenticated: Boolean = false, val models: List<String> = emptyList())
@Serializable data class ModelOptions(val provider: String = "", val model: String = "", val providers: List<ModelProvider> = emptyList())
@Serializable data class SkillSummary(val name: String, val description: String = "", val category: String? = null)
@Serializable data class SkillList(val data: List<SkillSummary> = emptyList())
@Serializable data class ToolsetSummary(val name: String, val label: String = name, val description: String = "", val enabled: Boolean = false, val configured: Boolean = false, val tools: List<String> = emptyList())
@Serializable data class ToolsetList(val data: List<ToolsetSummary> = emptyList())
@Serializable data class SkillsInventory(val skills: SkillList = SkillList(), val toolsets: ToolsetList = ToolsetList())
@Serializable data class Snapshot(val sessions: List<SessionSummary> = emptyList(), val cursor: Long = 0, val runtime: RuntimeReadiness = RuntimeReadiness(), val hermes: HermesCapabilities = HermesCapabilities(), val activeRuns: List<ActiveRun> = emptyList(), val pendingApprovals: List<ApprovalRequest> = emptyList())
@Serializable data class RuntimeReadiness(val bridge: Boolean = false, val hermes: Boolean = false, val coreReady: Boolean = false, val guiReady: Boolean = false, val tailscale: Boolean = false, val stt: Boolean = false, val summary: Boolean = false, val reason: String? = null)
@Serializable data class HermesCapabilities(val nativeSessions: Boolean = false, val sessionHistory: Boolean = false, val sessionStreaming: Boolean = false, val sessionRunControl: Boolean = false, val sessionApprovalResponse: Boolean = false, val jobs: Boolean = false, val models: Boolean = false, val skills: Boolean = false, val subagents: Boolean = false, val attachments: Boolean = false)
@Serializable data class ApprovalRequest(val requestId: String, val sessionId: String, val runId: String, val tool: String, val command: String? = null, val destination: String? = null, val rule: String? = null, val destructive: Boolean = false, val sensitive: Boolean = false, val choices: List<String> = emptyList())
@Serializable data class AgentAction(val kind: String, val deviceId: String, val idempotencyKey: String, val sessionId: String? = null, val runId: String? = null, val expectedState: String? = null, val createdAt: String, val payload: Map<String, String> = emptyMap())
@Serializable data class DurableEvent(val protocolVersion: String = "1.0", val eventId: String, val cursor: Long, val kind: String, val timestamp: String, val source: String, val sessionId: String? = null, val runId: String? = null, val payload: JsonElement? = null)
@Serializable data class EventReplay(
    val events: List<DurableEvent> = emptyList(),
    val nextCursor: Long = 0,
    val hasMore: Boolean = false,
    val oldestCursor: Long = 0,
    val latestCursor: Long = 0,
    val requiresSnapshot: Boolean = false,
)

/**
 * The Android process may be killed between receiving a bridge event and the
 * next connection attempt.  Keep the complete recovery boundary in one
 * serializable value so the foreground service and the Compose controller see
 * the same durable state after a reboot.
 */
@Serializable data class HermesPersistedState(
    val hasSnapshot: Boolean = false,
    val snapshot: Snapshot = Snapshot(),
    val events: List<DurableEvent> = emptyList(),
    val pendingEvents: List<DurableEvent> = emptyList(),
    val lastAckedCursor: Long = 0,
    val selectedSessionId: String? = null,
)
