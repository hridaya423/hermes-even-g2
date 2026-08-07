package com.honey.hermesg2

import android.Manifest
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.speech.tts.TextToSpeech
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import com.honey.hermesg2.data.*
import com.honey.hermesg2.service.HermesConnectionService
import kotlinx.coroutines.launch
import java.time.Instant
import java.util.Locale
import java.util.UUID

class MainActivity : ComponentActivity(), TextToSpeech.OnInitListener {
    private lateinit var tts: TextToSpeech
    override fun onCreate(savedInstanceState: Bundle?) { super.onCreate(savedInstanceState); tts = TextToSpeech(this, this); setContent { HermesTheme { Controller(intent.getStringExtra("sessionId")) } } }
    override fun onInit(status: Int) { if (status == TextToSpeech.SUCCESS) tts.language = Locale.UK }
    override fun onDestroy() { tts.shutdown(); super.onDestroy() }

    @OptIn(ExperimentalMaterial3Api::class)
    @Composable private fun Controller(deepLinkSession: String?) {
        var credentials by remember { mutableStateOf(SecureCredentials(this).load()) }
        if (credentials == null) return Pairing { SecureCredentials(this).save(it); credentials = it; startConnection() }
        val client = remember(credentials) { BridgeClient(credentials!!) }
        var snapshot by remember { mutableStateOf<Snapshot?>(null) }
        var selected by remember { mutableStateOf<SessionSummary?>(null) }
        var error by remember { mutableStateOf<String?>(null) }
        var prompt by remember { mutableStateOf("") }
        var tab by remember { mutableStateOf("Sessions") }
        var auxiliary by remember { mutableStateOf("") }
        val scope = rememberCoroutineScope()
        val notifications = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {}
        LaunchedEffect(Unit) { if (Build.VERSION.SDK_INT >= 33) notifications.launch(Manifest.permission.POST_NOTIFICATIONS); startConnection(); runCatching { client.snapshot() }.onSuccess { snapshot = it; selected = it.sessions.firstOrNull { session -> session.id == deepLinkSession } ?: it.sessions.firstOrNull() }.onFailure { error = it.message } }
        LaunchedEffect(tab) { auxiliary = when (tab) { "Jobs" -> if (snapshot?.hermes?.jobs == true) runCatching { client.jobs() }.getOrElse { it.message.orEmpty() } else "Jobs are not advertised by this Hermes build."; "Models" -> if (snapshot?.hermes?.models == true) runCatching { client.models() }.getOrElse { it.message.orEmpty() } else "Model options are unavailable."; "Skills" -> if (snapshot?.hermes?.skills == true) runCatching { client.skills() }.getOrElse { it.message.orEmpty() } else "Skills are not advertised by this Hermes build."; "Audit" -> runCatching { client.audit() }.getOrElse { it.message.orEmpty() }; else -> "" } }
        Scaffold(topBar = { TopAppBar(title = { Text("Hermes G2") }, actions = { TextButton(onClick = { scope.launch { client.action(AgentAction("createSession", credentials!!.deviceId, UUID.randomUUID().toString(), createdAt = Instant.now().toString(), payload = mapOf("title" to "G2 session"))); snapshot = client.snapshot() } }) { Text("New session") }; IconButton(onClick = { scope.launch { snapshot = client.snapshot() } }) { Icon(Icons.Default.Refresh, "Refresh") } }) }, bottomBar = { NavigationBar { listOf("Sessions" to Icons.Default.Chat, "Jobs" to Icons.Default.Schedule, "Models" to Icons.Default.Tune, "Skills" to Icons.Default.Build, "Audit" to Icons.Default.Security).forEach { (name, icon) -> NavigationBarItem(tab == name, { tab = name }, { Icon(icon, name) }, label = { Text(name) }) } } }) { padding ->
            if (tab != "Sessions") AuxiliaryPane(tab, auxiliary, Modifier.padding(padding)) else Row(Modifier.padding(padding).fillMaxSize()) {
                LazyColumn(Modifier.width(320.dp).fillMaxHeight()) { item { Readiness(snapshot?.runtime, error) }; snapshot?.pendingApprovals?.firstOrNull()?.let { approval -> item { ApprovalPane(approval, snapshot!!.hermes.sessionApprovalResponse) { choice -> scope.launch { runCatching { client.action(AgentAction(mapOf("once" to "approveOnce", "session" to "approveSession", "always" to "approveAlways", "deny" to "deny").getValue(choice), credentials!!.deviceId, UUID.randomUUID().toString(), approval.sessionId, approval.runId, "awaiting_approval", Instant.now().toString(), mapOf("requestId" to approval.requestId))) }.onSuccess { snapshot = client.snapshot() }.onFailure { error = it.message } } } } }; items(snapshot?.sessions.orEmpty(), key = { it.id }) { session -> ListItem(headlineContent = { Text(session.title) }, supportingContent = { Text("${session.source} · ${session.state}") }, leadingContent = { Icon(if (session.pinned) Icons.Default.PushPin else Icons.Default.ChatBubbleOutline, null) }, modifier = Modifier.fillMaxWidth(), trailingContent = { IconButton(onClick = { selected = session }) { Icon(Icons.Default.ChevronRight, "Open") } }); HorizontalDivider() } }
                VerticalDivider()
                SessionPane(selected, prompt, { prompt = it }, onSend = { text -> selected?.let { session -> scope.launch { runCatching { client.action(AgentAction(if (session.state == "busy") "queuePrompt" else "prompt", credentials!!.deviceId, UUID.randomUUID().toString(), session.id, createdAt = Instant.now().toString(), payload = mapOf("text" to text))) }.onSuccess { prompt = "" }.onFailure { error = it.message } } } }, onFork = { selected?.let { session -> scope.launch { client.action(AgentAction("forkSession", credentials!!.deviceId, UUID.randomUUID().toString(), session.id, createdAt = Instant.now().toString())); snapshot = client.snapshot() } } }, onPin = { selected?.let { session -> scope.launch { client.action(AgentAction(if (session.pinned) "unpinSession" else "pinSession", credentials!!.deviceId, UUID.randomUUID().toString(), session.id, createdAt = Instant.now().toString())); snapshot = client.snapshot() } } }, onSpeak = { selected?.latestAnswer?.let { text -> tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, "hermes-answer") } }, modifier = Modifier.weight(1f))
            }
        }
    }

    private fun startConnection() = ContextCompat.startForegroundService(this, Intent(this, HermesConnectionService::class.java))
    @Composable private fun Pairing(onPaired: (DeviceCredentials) -> Unit) { var origin by remember { mutableStateOf("") }; var code by remember { mutableStateOf("") }; var error by remember { mutableStateOf<String?>(null) }; var busy by remember { mutableStateOf(false) }; val scope = rememberCoroutineScope(); Column(Modifier.fillMaxSize().padding(32.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) { Text("Pair private bridge", style = MaterialTheme.typography.headlineLarge); Text("Enter the 90-second, single-use code shown by `hermes-g2-bridge pair android`. A revocable Android credential is generated and encrypted in Keystore; the Hermes master key never leaves the Mac mini."); OutlinedTextField(origin, { origin = it.trimEnd('/') }, label = { Text("Tailscale HTTPS origin") }, modifier = Modifier.fillMaxWidth()); OutlinedTextField(code, { code = it.filter(Char::isDigit).take(6) }, label = { Text("Pairing code") }, modifier = Modifier.fillMaxWidth()); error?.let { Text(it, color = MaterialTheme.colorScheme.error) }; Button(enabled = !busy && origin.startsWith("https://") && code.length == 6, onClick = { busy = true; scope.launch { runCatching { BridgeClient.exchange(origin, code, Build.MODEL) }.onSuccess(onPaired).onFailure { error = it.message; busy = false } } }) { Text(if (busy) "Pairing…" else "Pair device") } } }
    @Composable private fun Readiness(value: RuntimeReadiness?, error: String?) { Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) { Text(if (value?.coreReady == true) "Core ready" else "Core unavailable", fontWeight = FontWeight.Bold, color = if (value?.coreReady == true) Color(0xFF3A7D44) else MaterialTheme.colorScheme.error); Text(if (value?.guiReady == true) "GUI tools ready" else "GUI tools unavailable while logged out"); error?.let { Text(it, color = MaterialTheme.colorScheme.error) } } }
    @Composable private fun SessionPane(session: SessionSummary?, prompt: String, onPrompt: (String) -> Unit, onSend: (String) -> Unit, onFork: () -> Unit, onPin: () -> Unit, onSpeak: () -> Unit, modifier: Modifier = Modifier) { Column(modifier.padding(24.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) { Text(session?.title ?: "Select a session", style = MaterialTheme.typography.headlineMedium); session?.let { Text("${it.model ?: "Default model"} · ${it.source} · ${if (it.executionReady) "Execution ready" else "UNBOUND"}"); Row { TextButton(onClick = onPin) { Text(if (it.pinned) "Unpin from G2" else "Pin to G2") }; TextButton(onClick = onFork) { Text("Fork") }; TextButton(onClick = onSpeak, enabled = !it.latestAnswer.isNullOrBlank()) { Text("Speak") } }; Text(it.latestAnswer ?: "No completed answer yet.", modifier = Modifier.weight(1f)); Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) { OutlinedTextField(prompt, onPrompt, label = { Text("Continue this exact session") }, modifier = Modifier.weight(1f)); IconButton(onClick = { onSend(prompt) }, enabled = prompt.isNotBlank()) { Icon(Icons.Default.Send, "Send") } } } } }
    @Composable private fun ApprovalPane(value: ApprovalRequest, enabled: Boolean, onChoice: (String) -> Unit) { Card(Modifier.padding(12.dp)) { Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) { Text("Approval required", fontWeight = FontWeight.Bold); Text(value.tool); Text(value.command ?: value.destination ?: "Review this request privately."); if (!enabled) Text("This Hermes build cannot accept native session approvals.", color = MaterialTheme.colorScheme.error) else Row { value.choices.forEach { choice -> Button(onClick = { onChoice(choice) }, modifier = Modifier.padding(end = 6.dp)) { Text(choice.uppercase()) } } } } } }
    @Composable private fun AuxiliaryPane(title: String, content: String, modifier: Modifier = Modifier) { Column(modifier.fillMaxSize().padding(24.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) { Text(title, style = MaterialTheme.typography.headlineMedium); Text("Read-only capability view; actions only appear when Hermes advertises them."); LazyColumn(Modifier.fillMaxSize()) { item { Text(content.ifBlank { "Loading…" }, style = MaterialTheme.typography.bodySmall) } } } }
    @Composable private fun HermesTheme(content: @Composable () -> Unit) { MaterialTheme(colorScheme = darkColorScheme(primary = Color(0xFF77E27B), background = Color(0xFF071008), surface = Color(0xFF0D180E)), content = content) }
}
