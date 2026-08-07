package com.honey.hermesg2.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources

class BridgeClient(private val credentials: DeviceCredentials, private val client: OkHttpClient = OkHttpClient()) {
    private val json = Json { ignoreUnknownKeys = true }
    private fun request(path: String) = Request.Builder().url("${credentials.origin}$path").header("Authorization", "Bearer ${credentials.credential}").header("X-Device-Id", credentials.deviceId)
    suspend fun snapshot(): Snapshot = get("/v1/snapshot")
    suspend fun sessions(): List<SessionSummary> = get("/v1/sessions")
    suspend fun messages(sessionId: String, limit: Int = 100, offset: Int = 0): MessagePage = get("/v1/sessions/$sessionId/messages?limit=$limit&offset=$offset")
    suspend fun jobs(): JobList = get("/v1/jobs")
    suspend fun models(): String = raw("/v1/models")
    suspend fun skills(): String = raw("/v1/skills")
    suspend fun audit(): String = raw("/v1/audit")
    suspend fun devices(): List<DeviceRecord> = get("/v1/devices")
    suspend fun revokeDevice(deviceId: String): String = withContext(Dispatchers.IO) {
        client.newCall(request("/v1/devices/$deviceId/revoke").post(ByteArray(0).toRequestBody(null)).build()).execute().use { response ->
            if (!response.isSuccessful) error(response.body?.string() ?: "Bridge ${response.code}")
            response.body?.string().orEmpty()
        }
    }
    suspend fun action(value: AgentAction): String = withContext(Dispatchers.IO) {
        val body = json.encodeToString(AgentAction.serializer(), value).toRequestBody("application/json".toMediaType())
        client.newCall(request("/v1/actions").post(body).build()).execute().use { response -> if (!response.isSuccessful) error(response.body?.string() ?: "Bridge ${response.code}"); response.body?.string().orEmpty() }
    }
    fun channel(cursor: Long, listener: WebSocketListener): WebSocket {
        val url = credentials.origin.replaceFirst("https://", "wss://").replaceFirst("http://", "ws://")
        val socketRequest = Request.Builder().url("$url/v1/channel?after=$cursor")
            .header("Authorization", "Bearer ${credentials.credential}")
            .header("X-Device-Id", credentials.deviceId)
            .build()
        return client.newWebSocket(socketRequest, listener)
    }
    fun events(cursor: Long, listener: EventSourceListener): EventSource {
        val eventRequest = request("/v1/events?after=$cursor")
            .header("Accept", "text/event-stream")
            .build()
        return EventSources.createFactory(client).newEventSource(eventRequest, listener)
    }
    private suspend inline fun <reified T> get(path: String): T = withContext(Dispatchers.IO) { client.newCall(request(path).build()).execute().use { response -> if (!response.isSuccessful) error("Bridge ${response.code}"); json.decodeFromString(response.body!!.string()) } }
    private suspend fun raw(path: String): String = withContext(Dispatchers.IO) { client.newCall(request(path).build()).execute().use { response -> if (!response.isSuccessful) error("Bridge ${response.code}"); response.body!!.string() } }

    companion object {
        suspend fun exchange(origin: String, code: String, deviceName: String): DeviceCredentials = withContext(Dispatchers.IO) {
            val json = Json { ignoreUnknownKeys = true }
            val body = json.encodeToString(PairingRequest.serializer(), PairingRequest(code, deviceName)).toRequestBody("application/json".toMediaType())
            OkHttpClient().newCall(Request.Builder().url("${origin.trimEnd('/')}/v1/pairings/exchange").post(body).build()).execute().use { response ->
                if (!response.isSuccessful) error(response.body?.string() ?: "Pairing failed (${response.code})")
                val paired = json.decodeFromString<PairingResponse>(response.body!!.string())
                DeviceCredentials(origin.trimEnd('/'), paired.deviceId, paired.credential)
            }
        }
    }
}
