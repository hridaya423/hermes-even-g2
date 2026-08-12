package com.honey.hermesg2.data

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.emptyPreferences
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put

private val Context.hermesStateDataStore: DataStore<Preferences> by preferencesDataStore(name = "hermes_g2_state")

/**
 * Durable state shared by the foreground service, WorkManager and the Compose
 * controller.  DataStore serializes updates, while the exposed StateFlow makes
 * the last committed snapshot immediately consumable by UI code.
 */
class HermesStateRepository private constructor(context: Context) {
    private val appContext = context.applicationContext
    private val dataStore = appContext.hermesStateDataStore
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val updateMutex = Mutex()
    private val json = Json {
        ignoreUnknownKeys = true
        encodeDefaults = true
    }
    private val stateKey = stringPreferencesKey("state")

    val state: StateFlow<HermesPersistedState> = dataStore.data
        .catch { emit(emptyPreferences()) }
        .map(::decode)
        .stateIn(scope, SharingStarted.Eagerly, HermesPersistedState())

    suspend fun current(): HermesPersistedState = dataStore.data
        .catch { emit(emptyPreferences()) }
        .map(::decode)
        .first()

    suspend fun persistSnapshot(snapshot: Snapshot): HermesPersistedState = update { current ->
        current.copy(
            hasSnapshot = true,
            snapshot = snapshot,
            lastAckedCursor = maxOf(current.lastAckedCursor, snapshot.cursor),
            // A snapshot contains the authoritative pending approval set. Any
            // event-only attention records must be replayed after this cursor.
            pendingEvents = emptyList(),
        )
    }

    suspend fun persistEvent(event: DurableEvent): HermesPersistedState = update { current ->
        HermesStateReducer.apply(current, boundEvent(event))
    }

    suspend fun persistSelectedSession(sessionId: String?): HermesPersistedState = update { current ->
        current.copy(selectedSessionId = sessionId)
    }

    private suspend fun update(transform: (HermesPersistedState) -> HermesPersistedState): HermesPersistedState =
        updateMutex.withLock {
            var committed: HermesPersistedState? = null
            dataStore.edit { preferences ->
                val next = transform(decode(preferences))
                preferences[stateKey] = json.encodeToString(HermesPersistedState.serializer(), next)
                committed = next
            }
            committed ?: error("Hermes state commit did not return a value")
        }

    private fun decode(preferences: Preferences): HermesPersistedState =
        preferences[stateKey]?.let { encoded ->
            runCatching { json.decodeFromString(HermesPersistedState.serializer(), encoded) }.getOrNull()
        } ?: HermesPersistedState()

    private fun boundEvent(event: DurableEvent): DurableEvent {
        val encoded = runCatching { json.encodeToString(DurableEvent.serializer(), event) }.getOrNull()
        if (encoded == null || encoded.length <= MAX_EVENT_BYTES) return event
        return event.copy(
            payload = buildJsonObject {
                put("truncated", true)
                put("kind", event.kind)
            },
        )
    }

    companion object {
        private const val MAX_EVENT_BYTES = 32_000
        private const val HOLDER_LOCK = "hermes-state-repository"

        @Volatile private var instance: HermesStateRepository? = null

        fun get(context: Context): HermesStateRepository =
            instance ?: synchronized(HOLDER_LOCK) {
                instance ?: HermesStateRepository(context).also { instance = it }
            }
    }
}

internal object HermesStateReducer {
    private const val MAX_EVENTS = 256
    private const val MAX_PENDING_EVENTS = 128

    fun apply(state: HermesPersistedState, event: DurableEvent): HermesPersistedState {
        val eventAlreadyStored = state.events.any { it.eventId == event.eventId }
        val events = if (eventAlreadyStored) {
            state.events
        } else {
            (state.events + event).sortedBy { it.cursor }.takeLast(MAX_EVENTS)
        }
        val pending = when {
            event.kind == "attention.created" || event.kind == "approval.required" -> {
                if (state.pendingEvents.any { it.eventId == event.eventId }) state.pendingEvents
                else (state.pendingEvents + event).takeLast(MAX_PENDING_EVENTS)
            }
            event.kind == "attention.resolved" || event.kind == "approval.resolved" -> {
                val requestId = event.payload?.requestId()
                state.pendingEvents.filterNot { pendingEvent ->
                    val sameRequest = requestId != null && pendingEvent.payload?.requestId() == requestId
                    val sameRun = requestId == null && pendingEvent.sessionId == event.sessionId && pendingEvent.runId == event.runId
                    sameRequest || sameRun
                }
            }
            else -> state.pendingEvents
        }
        return state.copy(
            events = events,
            pendingEvents = pending,
            lastAckedCursor = maxOf(state.lastAckedCursor, event.cursor),
        )
    }

    private fun JsonElement.requestId(): String? =
        jsonObject["requestId"]?.jsonPrimitive?.contentOrNull
}
