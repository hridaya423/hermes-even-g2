package com.honey.hermesg2.service

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.os.IBinder
import androidx.core.app.NotificationCompat
import com.honey.hermesg2.MainActivity
import com.honey.hermesg2.data.BridgeClient
import com.honey.hermesg2.data.DurableEvent
import com.honey.hermesg2.data.DurableCursor
import com.honey.hermesg2.data.SecureCredentials
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import java.util.concurrent.atomic.AtomicLong
import kotlin.math.min
import kotlin.random.Random

class HermesConnectionService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val cursor = AtomicLong(0)
    private var reconnect: Job? = null
    private var socket: WebSocket? = null
    private var eventSource: EventSource? = null
    private lateinit var durableCursor: DurableCursor
    private val json = Json { ignoreUnknownKeys = true }

    override fun onCreate() {
        super.onCreate()
        durableCursor = DurableCursor(this)
        cursor.set(durableCursor.load() ?: 0)
        channels()
        startForeground(CONNECTION_NOTIFICATION, statusNotification("Connecting privately…"))
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int { connect(); return START_STICKY }
    override fun onBind(intent: Intent?): IBinder? = null
    override fun onDestroy() { reconnect?.cancel(); socket?.cancel(); eventSource?.cancel(); scope.coroutineContext[Job]?.cancel(); super.onDestroy() }

    private fun connect() {
        reconnect?.cancel()
        val credentials = SecureCredentials(this).load() ?: return
        val bridge = BridgeClient(credentials)
        scope.launch {
            if (durableCursor.load() == null) {
                runCatching { bridge.snapshot() }.onSuccess {
                    cursor.set(it.cursor)
                    durableCursor.persistBeforeAcknowledge(it.cursor)
                }
            }
            open(bridge, 0)
        }
    }

    private fun open(bridge: BridgeClient, attempt: Int) {
        eventSource?.cancel()
        socket?.cancel()
        socket = bridge.channel(cursor.get(), object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) { updateStatus("Connected to Hermes") }
            override fun onMessage(webSocket: WebSocket, text: String) {
                handleEvent(text) { event -> webSocket.send("{\"type\":\"ack\",\"cursor\":${event.cursor}}") }
            }
            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = recover(bridge, attempt + 1)
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) = recover(bridge, attempt + 1)
        })
    }

    private fun openSse(bridge: BridgeClient, attempt: Int) {
        socket?.cancel()
        eventSource?.cancel()
        eventSource = bridge.events(cursor.get(), object : EventSourceListener() {
            override fun onOpen(eventSource: EventSource, response: Response) = updateStatus("Connected to Hermes (fallback)")
            override fun onEvent(eventSource: EventSource, id: String?, type: String?, data: String) = handleEvent(data)
            override fun onClosed(eventSource: EventSource) = schedule(bridge, attempt + 1)
            override fun onFailure(eventSource: EventSource, t: Throwable?, response: Response?) = schedule(bridge, attempt + 1)
        })
    }

    private fun handleEvent(text: String, acknowledge: ((DurableEvent) -> Unit)? = null) {
        runCatching { json.decodeFromString(DurableEvent.serializer(), text) }.onSuccess { event ->
            cursor.updateAndGet { maxOf(it, event.cursor) }
            durableCursor.persistBeforeAcknowledge(cursor.get())
            acknowledge?.invoke(event)
            notifyIfActionable(event)
        }
    }

    private fun recover(bridge: BridgeClient, attempt: Int) {
        if (attempt >= 3) schedule(bridge, attempt, useSse = true) else schedule(bridge, attempt)
    }

    private fun schedule(bridge: BridgeClient, attempt: Int, useSse: Boolean = false) {
        reconnect?.cancel()
        reconnect = scope.launch {
            delay(min(60_000L, 1_000L shl min(attempt, 6)) + Random.nextLong(500))
            if (isActive) {
                if (useSse) openSse(bridge, attempt) else open(bridge, attempt)
            }
        }
    }

    private fun notifyIfActionable(event: DurableEvent) {
        val policy = NotificationPolicy.forEvent(event) ?: return
        val intent = Intent(this, MainActivity::class.java).putExtra("sessionId", event.sessionId).putExtra("runId", event.runId)
        val pending = PendingIntent.getActivity(this, event.eventId.hashCode(), intent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val notification = NotificationCompat.Builder(this, ATTENTION_CHANNEL).setSmallIcon(android.R.drawable.stat_notify_chat).setContentTitle(policy.title).setContentText("Open Hermes G2 to review privately").setContentIntent(pending).setAutoCancel(true).setPriority(NotificationCompat.PRIORITY_HIGH).build()
        getSystemService(NotificationManager::class.java).notify(event.eventId.hashCode(), notification)
    }

    private fun channels() { getSystemService(NotificationManager::class.java).createNotificationChannels(listOf(NotificationChannel(CONNECTION_CHANNEL, "Hermes private connection", NotificationManager.IMPORTANCE_LOW), NotificationChannel(ATTENTION_CHANNEL, "Hermes attention", NotificationManager.IMPORTANCE_HIGH))) }
    private fun statusNotification(text: String) = NotificationCompat.Builder(this, CONNECTION_CHANNEL).setSmallIcon(android.R.drawable.stat_sys_upload_done).setContentTitle("Hermes G2").setContentText(text).setOngoing(true).build()
    private fun updateStatus(text: String) = getSystemService(NotificationManager::class.java).notify(CONNECTION_NOTIFICATION, statusNotification(text))
    companion object { const val CONNECTION_CHANNEL = "hermes_connection"; const val ATTENTION_CHANNEL = "hermes_attention"; const val CONNECTION_NOTIFICATION = 4102 }
}
