package com.honey.hermesg2.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

@Serializable data class DeviceCredentials(val origin: String, val deviceId: String, val credential: String)
@Serializable data class PairingRequest(val code: String, val deviceName: String, val deviceKind: String = "android")
@Serializable data class PairingResponse(val deviceId: String, val credential: String, val scopes: List<String>)
@Serializable data class SessionSummary(val id: String, val title: String = "Untitled", val source: String = "unknown", val model: String? = null, val state: String = "idle", val updatedAt: String = "", val pinned: Boolean = false, val executionReady: Boolean = true, val latestAnswer: String? = null)
@Serializable data class AgentMessage(val id: String, val sessionId: String, val role: String, val content: String, val reasoning: String? = null, val timestamp: String? = null, val finishReason: String? = null, val toolName: String? = null, val tokenCount: Int? = null)
@Serializable data class MessagePage(val data: List<AgentMessage> = emptyList(), val total: Int = 0, val hasMore: Boolean = false, val offset: Int = 0, val limit: Int = 100)
@Serializable data class Snapshot(val sessions: List<SessionSummary> = emptyList(), val cursor: Long = 0, val runtime: RuntimeReadiness = RuntimeReadiness(), val hermes: HermesCapabilities = HermesCapabilities(), val pendingApprovals: List<ApprovalRequest> = emptyList())
@Serializable data class RuntimeReadiness(val bridge: Boolean = false, val hermes: Boolean = false, val coreReady: Boolean = false, val guiReady: Boolean = false, val tailscale: Boolean = false, val stt: Boolean = false, val summary: Boolean = false, val reason: String? = null)
@Serializable data class HermesCapabilities(val nativeSessions: Boolean = false, val sessionHistory: Boolean = false, val sessionStreaming: Boolean = false, val sessionRunControl: Boolean = false, val sessionApprovalResponse: Boolean = false, val jobs: Boolean = false, val models: Boolean = false, val skills: Boolean = false, val subagents: Boolean = false, val attachments: Boolean = false)
@Serializable data class ApprovalRequest(val requestId: String, val sessionId: String, val runId: String, val tool: String, val command: String? = null, val destination: String? = null, val rule: String? = null, val destructive: Boolean = false, val sensitive: Boolean = false, val choices: List<String> = emptyList())
@Serializable data class AgentAction(val kind: String, val deviceId: String, val idempotencyKey: String, val sessionId: String? = null, val runId: String? = null, val expectedState: String? = null, val createdAt: String, val payload: Map<String, String> = emptyMap())
@Serializable data class DurableEvent(val protocolVersion: String = "1.0", val eventId: String, val cursor: Long, val kind: String, val timestamp: String, val source: String, val sessionId: String? = null, val runId: String? = null, val payload: JsonElement? = null)
