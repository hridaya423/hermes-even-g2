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
import com.honey.hermesg2.data.BridgeWireFrame
import com.honey.hermesg2.data.BridgeWireFrameParser
import com.honey.hermesg2.data.DurableEvent
import com.honey.hermesg2.data.AgentAction
import com.honey.hermesg2.data.HermesStateRepository
import com.honey.hermesg2.data.SecureCredentials
import com.honey.hermesg2.data.Snapshot
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import java.util.concurrent.atomic.AtomicLong
import java.util.concurrent.atomic.AtomicBoolean
import java.time.Instant
import kotlin.math.min
import kotlin.random.Random

class HermesConnectionService : Service() {
    private data class QueuedEvent(val event: DurableEvent, val acknowledge: ((Long) -> Unit)?)

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val eventQueue = Channel<QueuedEvent>(capacity = 64)
    private val eventProcessingMutex = Mutex()
    private val cursor = AtomicLong(0)
    private var reconnect: Job? = null
    private var socket: WebSocket? = null
    private var eventSource: EventSource? = null
    private lateinit var stateRepository: HermesStateRepository
    private val snapshotInFlight = AtomicBoolean(false)

    override fun onCreate() {
        super.onCreate()
        stateRepository = HermesStateRepository.get(this)
        scope.launch {
            for (queued in eventQueue) processEvent(queued.event, queued.acknowledge)
        }
        channels()
        startForeground(CONNECTION_NOTIFICATION, statusNotification("Connecting privately…"))
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int { connect(); return START_STICKY }
    override fun onBind(intent: Intent?): IBinder? = null
    override fun onDestroy() { reconnect?.cancel(); socket?.cancel(); eventSource?.cancel(); eventQueue.close(); scope.coroutineContext[Job]?.cancel(); super.onDestroy() }

    private fun connect() {
        reconnect?.cancel()
        val credentials = SecureCredentials(this).load() ?: return
        val bridge = BridgeClient(credentials)
        scope.launch {
            val persisted = stateRepository.current()
            cursor.set(persisted.lastAckedCursor)
            if (!persisted.hasSnapshot) {
                runCatching { bridge.snapshot() }.onSuccess { snapshot ->
                    commitSnapshot(snapshot)
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
                handleWireMessage(text, bridge) { eventCursor ->
                    webSocket.send("{\"type\":\"ack\",\"cursor\":$eventCursor}")
                }
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
            override fun onEvent(eventSource: EventSource, id: String?, type: String?, data: String) = handleWireMessage(data, bridge) { eventCursor ->
                acknowledge(bridge, eventCursor, "sse-$eventCursor")
            }
            override fun onClosed(eventSource: EventSource) = recoverSse(bridge, attempt + 1)
            override fun onFailure(eventSource: EventSource, t: Throwable?, response: Response?) = recoverSse(bridge, attempt + 1)
        })
    }

    private fun recoverSse(bridge: BridgeClient, attempt: Int) {
        if (attempt >= 6) pollThenReconnect(bridge) else schedule(bridge, attempt, useSse = true)
    }

    private fun pollThenReconnect(bridge: BridgeClient) {
        reconnect?.cancel()
        socket?.cancel()
        eventSource?.cancel()
        reconnect = scope.launch {
            updateStatus("Hermes polling fallback")
            repeat(12) {
                val replay = runCatching { bridge.replayEvents(cursor.get()) }.getOrNull()
                if (replay != null) {
                    if (replay.requiresSnapshot) {
                        if (reconcile(bridge)) {
                            open(bridge, 0)
                            return@launch
                        }
                        delay(250)
                        return@repeat
                    }
                    replay.events.forEach { event ->
                        processEvent(event, null)
                    }
                    if (replay.events.isNotEmpty()) acknowledge(bridge, replay.nextCursor, "poll-${replay.nextCursor}")
                    if (replay.hasMore) {
                        delay(100)
                        return@repeat
                    }
                }
                delay(5_000)
            }
            if (isActive) open(bridge, 0)
        }
    }

    private fun acknowledge(bridge: BridgeClient, value: Long, key: String) {
        scope.launch {
            val credentials = SecureCredentials(this@HermesConnectionService).load() ?: return@launch
            runCatching {
                bridge.action(
                    AgentAction(
                        kind = "acknowledge",
                        deviceId = credentials.deviceId,
                        idempotencyKey = key,
                        createdAt = Instant.now().toString(),
                        payload = mapOf("cursor" to value.toString()),
                    )
                )
            }
        }
    }

    /**
     * The bridge deliberately sends replay.gap as a control envelope rather
     * than a DurableEvent. Decode that envelope first so a gap can never move
     * the local cursor or get acknowledged as if it were real work.
     */
    private fun handleWireMessage(
        text: String,
        bridge: BridgeClient,
        acknowledge: ((Long) -> Unit)? = null,
    ) {
        val frame = BridgeWireFrameParser.parse(text)
        if (frame is BridgeWireFrame.ReplayGap) {
            requestSnapshot(bridge)
            return
        }
        if (frame !is BridgeWireFrame.Event) return
        val event = frame.value
        if (eventQueue.trySend(QueuedEvent(event, acknowledge)).isFailure) {
            // Never acknowledge an event that could not enter the durable
            // processing queue. Reconnect from the committed cursor instead.
            requestSnapshot(bridge)
        }
    }

    /**
     * Persist the event before acknowledging it to Hermes. DataStore's update
     * transaction is the recovery boundary: a killed process either sees the
     * old cursor and replays the event, or sees the committed event and cursor.
     */
    private suspend fun processEvent(event: DurableEvent, acknowledge: ((Long) -> Unit)?) {
        eventProcessingMutex.withLock {
            val committed = stateRepository.persistEvent(event)
            cursor.set(committed.lastAckedCursor)
            acknowledge?.invoke(committed.lastAckedCursor)
            notifyIfActionable(event)
        }
    }

    private suspend fun commitSnapshot(snapshot: Snapshot) {
        val committed = stateRepository.persistSnapshot(snapshot)
        cursor.set(committed.lastAckedCursor)
    }

    private suspend fun reconcile(bridge: BridgeClient): Boolean = runCatching {
        commitSnapshot(bridge.snapshot())
    }.isSuccess

    private fun requestSnapshot(bridge: BridgeClient) {
        if (!snapshotInFlight.compareAndSet(false, true)) return
        reconnect?.cancel()
        socket?.cancel()
        eventSource?.cancel()
        reconnect = scope.launch {
            updateStatus("Hermes replay gap — resyncing")
            val success = reconcile(bridge)
            snapshotInFlight.set(false)
            if (success && isActive) {
                updateStatus("Reconnected after snapshot")
                open(bridge, 0)
            } else if (isActive) {
                schedule(bridge, 1)
            }
        }
    }

    private fun recover(bridge: BridgeClient, attempt: Int) {
        if (snapshotInFlight.get()) return
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
