package com.honey.hermesg2

import android.Manifest
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Chat
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.collectAsState
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import com.honey.hermesg2.data.*
import com.honey.hermesg2.service.HermesConnectionService
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.Job
import java.time.Instant
import java.util.Locale
import java.util.UUID

class MainActivity : ComponentActivity(), TextToSpeech.OnInitListener {
    private lateinit var tts: TextToSpeech
    private val deepLinkState = MutableStateFlow(DeepLinkTarget())
    private val speakingState = MutableStateFlow(false)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        deepLinkState.value = DeepLinkTarget(intent.getStringExtra("sessionId"), intent.getStringExtra("runId"))
        tts = TextToSpeech(this, this)
        setContent {
            HermesTheme {
                val deepLink by deepLinkState.collectAsState()
                Controller(deepLink)
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        deepLinkState.value = DeepLinkTarget(intent.getStringExtra("sessionId"), intent.getStringExtra("runId"))
    }

    override fun onInit(status: Int) {
        if (status != TextToSpeech.SUCCESS) return
        tts.language = Locale.UK
        tts.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(utteranceId: String?) { speakingState.value = true }
            override fun onDone(utteranceId: String?) { speakingState.value = false }
            override fun onError(utteranceId: String?) { speakingState.value = false }
        })
    }

    override fun onDestroy() {
        speakingState.value = false
        if (::tts.isInitialized) {
            tts.stop()
            tts.shutdown()
        }
        super.onDestroy()
    }

    @OptIn(ExperimentalMaterial3Api::class)
    @Composable private fun Controller(deepLink: DeepLinkTarget) {
        var credentials by remember { mutableStateOf(SecureCredentials(this).load()) }
        if (credentials == null) return Pairing { SecureCredentials(this).save(it); credentials = it; startConnection() }
        val client = remember(credentials) { BridgeClient(credentials!!) }
        val stateRepository = remember { HermesStateRepository.get(this@MainActivity) }
        val persistedState by stateRepository.state.collectAsState()
        var snapshot by remember { mutableStateOf<Snapshot?>(null) }
        var selected by remember { mutableStateOf<SessionSummary?>(null) }
        var history by remember { mutableStateOf<MessagePage?>(null) }
        var error by remember { mutableStateOf<String?>(null) }
        var prompt by remember { mutableStateOf("") }
        var tab by remember { mutableStateOf("Sessions") }
        var auxiliary by remember { mutableStateOf("") }
        var jobs by remember { mutableStateOf<JobList?>(null) }
        var devices by remember { mutableStateOf<List<DeviceRecord>?>(null) }
        var modelOptions by remember { mutableStateOf<ModelOptions?>(null) }
        var skillsInventory by remember { mutableStateOf<SkillsInventory?>(null) }
        var pendingAttachments by remember { mutableStateOf<List<AttachmentUpload>>(emptyList()) }
        var attachmentTargetSessionId by remember { mutableStateOf<String?>(null) }
        var attachmentBusy by remember { mutableStateOf(false) }
        var attachmentStatus by remember { mutableStateOf<String?>(null) }
        var attachmentUploadJob by remember { mutableStateOf<Job?>(null) }
        var speechConfirmation by remember { mutableStateOf(false) }
        val speaking by speakingState.collectAsState()
        val scope = rememberCoroutineScope()
        val notifications = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {}
        val attachmentPicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris ->
            val targetSessionId = attachmentTargetSessionId
            if (targetSessionId == null || uris.isEmpty()) {
                if (targetSessionId != null) attachmentStatus = "No files selected."
            } else {
                val accepted = AttachmentSelectionPolicy.acceptedCount(pendingAttachments.size, uris.size)
                if (accepted < uris.size) attachmentStatus = "Only $accepted of ${uris.size} selected files fit the ${AttachmentSelectionPolicy.MAX_ATTACHMENTS}-file limit."
                val uploadJob = scope.launch {
                    attachmentBusy = true
                    try {
                        uris.take(accepted).forEach { uri ->
                            val uploaded = client.uploadAttachment(
                                targetSessionId,
                                ContentResolverAttachmentSource(contentResolver, uri, targetSessionId),
                            )
                            if (selected?.id == targetSessionId) {
                                pendingAttachments = (pendingAttachments + uploaded).take(AttachmentSelectionPolicy.MAX_ATTACHMENTS)
                            }
                        }
                        if (selected?.id == targetSessionId) {
                            attachmentStatus = if (pendingAttachments.isEmpty()) "No files uploaded." else "${pendingAttachments.size} file${if (pendingAttachments.size == 1) "" else "s"} attached (${pendingAttachments.sumOf { it.size }.let(AttachmentSelectionPolicy::humanSize)})."
                            error = null
                        }
                    } catch (cancelled: CancellationException) {
                        if (selected?.id == targetSessionId) attachmentStatus = "Attachment upload cancelled."
                    } catch (failure: Throwable) {
                        if (selected?.id == targetSessionId) {
                            attachmentStatus = "Attachment upload failed. Review the attached list before sending."
                            error = failure.message
                        }
                    } finally {
                        attachmentBusy = false
                        attachmentUploadJob = null
                    }
                }
                attachmentUploadJob = uploadJob
            }
        }
        LaunchedEffect(persistedState.hasSnapshot, persistedState.selectedSessionId) {
            if (snapshot == null && persistedState.hasSnapshot) {
                snapshot = persistedState.snapshot
                selected = persistedState.snapshot.sessions.firstOrNull { session -> session.id == deepLink.sessionId }
                    ?: persistedState.snapshot.sessions.firstOrNull { session -> session.id == persistedState.selectedSessionId }
                    ?: persistedState.snapshot.sessions.firstOrNull()
            }
        }
        LaunchedEffect(Unit) {
            if (Build.VERSION.SDK_INT >= 33) notifications.launch(Manifest.permission.POST_NOTIFICATIONS)
            startConnection()
            val restored = stateRepository.current()
            runCatching { client.snapshot() }.onSuccess { fresh ->
                stateRepository.persistSnapshot(fresh)
                snapshot = fresh
                selected = fresh.sessions.firstOrNull { session -> session.id == deepLink.sessionId }
                    ?: fresh.sessions.firstOrNull { session -> session.id == restored.selectedSessionId }
                    ?: fresh.sessions.firstOrNull()
            }.onFailure { error = it.message }
        }
        LaunchedEffect(tab, snapshot?.hermes) { auxiliary = when (tab) { "Jobs" -> { jobs = if (snapshot?.hermes?.jobs == true) runCatching { client.jobs() }.getOrElse { error = it.message; null } else null; if (snapshot?.hermes?.jobs == true) "" else "Jobs are not advertised by this Hermes build." }; "Models" -> { modelOptions = if (snapshot?.hermes?.models == true) runCatching { client.modelOptions() }.getOrElse { error = it.message; null } else null; if (snapshot?.hermes?.models == true) "" else "Model options are unavailable." }; "Skills" -> { skillsInventory = if (snapshot?.hermes?.skills == true) runCatching { client.skills() }.getOrElse { error = it.message; null } else null; if (snapshot?.hermes?.skills == true) "" else "Skills are not advertised by this Hermes build." }; "Security" -> { devices = runCatching { client.devices() }.getOrElse { error = it.message; null }; runCatching { client.audit() }.getOrElse { it.message.orEmpty() } }; else -> "" } }
        LaunchedEffect(deepLink.sessionId, snapshot?.sessions) {
            deepLink.sessionId?.let { id -> snapshot?.sessions?.firstOrNull { it.id == id }?.let { selected = it } }
        }
        LaunchedEffect(selected?.id) { stateRepository.persistSelectedSession(selected?.id); pendingAttachments = emptyList(); attachmentStatus = null; history = selected?.let { session -> runCatching { client.messages(session.id) }.getOrElse { error = it.message; null } } }
        val loadOlderHistory = {
            val session = selected
            val current = history
            if (session != null && current != null && current.hasMore) scope.launch {
                runCatching { client.messages(session.id, limit = current.limit, offset = current.offset + current.data.size) }
                    .onSuccess { older ->
                        val merged = (current.data + older.data).distinctBy { it.id }
                        history = current.copy(data = merged, total = older.total, hasMore = older.hasMore)
                        error = null
                    }
                    .onFailure { error = it.message }
            }
        }
        if (speechConfirmation) AlertDialog(
            onDismissRequest = { speechConfirmation = false },
            title = { Text(if (speaking) "Stop phone playback?" else "Speak latest answer?") },
            text = { Text(if (speaking) "Stop Hermes playback now?" else "This reads the newest assistant answer aloud through the phone speaker. Use headphones or a private place if the session may contain sensitive information.") },
            confirmButton = {
                Button(onClick = {
                    if (speaking) {
                        tts.stop()
                        speakingState.value = false
                    } else {
                        val text = SpeechSelectionPolicy.latestAssistant(history?.data.orEmpty())?.content
                        if (!text.isNullOrBlank() && tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, "hermes-answer") == TextToSpeech.ERROR) speakingState.value = false
                    }
                    speechConfirmation = false
                }) { Text(if (speaking) "Stop" else "Speak") }
            },
            dismissButton = { TextButton(onClick = { speechConfirmation = false }) { Text("Cancel") } },
        )
        Scaffold(topBar = { TopAppBar(title = { Text("Hermes G2") }, actions = { TextButton(onClick = { scope.launch { client.action(AgentAction("createSession", credentials!!.deviceId, UUID.randomUUID().toString(), createdAt = Instant.now().toString(), payload = mapOf("title" to "G2 session"))); snapshot = client.snapshot() } }) { Text("New session") }; IconButton(onClick = { scope.launch { snapshot = client.snapshot() } }) { Icon(Icons.Default.Refresh, "Refresh") } }) }, bottomBar = { NavigationBar { listOf("Sessions" to Icons.AutoMirrored.Filled.Chat, "Jobs" to Icons.Default.Schedule, "Models" to Icons.Default.Tune, "Skills" to Icons.Default.Build, "Security" to Icons.Default.Security).forEach { (name, icon) -> NavigationBarItem(tab == name, { tab = name }, { Icon(icon, name) }, label = { Text(name) }) } } }) { padding ->
            if (tab == "Jobs") JobsPane(jobs, auxiliary, onAction = { kind, jobId -> scope.launch { runCatching { client.action(AgentAction(kind, credentials!!.deviceId, UUID.randomUUID().toString(), createdAt = Instant.now().toString(), payload = mapOf("jobId" to jobId))) }.onSuccess { jobs = client.jobs() }.onFailure { error = it.message } } }, Modifier.padding(padding)) else if (tab == "Models") ModelPane(modelOptions, selected, onSelect = { provider, model -> selected?.let { session -> scope.launch { runCatching { client.action(AgentAction("setSessionModel", credentials!!.deviceId, UUID.randomUUID().toString(), session.id, createdAt = Instant.now().toString(), payload = mapOf("provider" to provider, "model" to model))) }.onSuccess { snapshot = client.snapshot(); selected = snapshot?.sessions?.firstOrNull { it.id == session.id } }.onFailure { error = it.message } } } }, Modifier.padding(padding)) else if (tab == "Skills") SkillsPane(skillsInventory, auxiliary, Modifier.padding(padding)) else if (tab == "Security") SecurityPane(devices, credentials!!.deviceId, auxiliary, onRevoke = { deviceId -> scope.launch { runCatching { client.revokeDevice(deviceId) }.onSuccess { devices = client.devices() }.onFailure { error = it.message } } }, Modifier.padding(padding)) else if (tab != "Sessions") AuxiliaryPane(tab, auxiliary, Modifier.padding(padding)) else Row(Modifier.padding(padding).fillMaxSize()) {
                LazyColumn(Modifier.width(320.dp).fillMaxHeight()) { item { Readiness(snapshot?.runtime, error) }; snapshot?.pendingApprovals?.firstOrNull { approval -> deepLink.runId == null || approval.runId == deepLink.runId }?.let { approval -> item { ApprovalPane(approval, snapshot!!.hermes.sessionApprovalResponse) { choice -> ApprovalPolicy.actionKindFor(choice)?.let { kind -> scope.launch { runCatching { client.action(AgentAction(kind, credentials!!.deviceId, UUID.randomUUID().toString(), approval.sessionId, approval.runId, "awaiting_approval", Instant.now().toString(), mapOf("requestId" to approval.requestId))) }.onSuccess { snapshot = client.snapshot() }.onFailure { error = it.message } } } } } }; items(snapshot?.sessions.orEmpty(), key = { it.id }) { session -> ListItem(headlineContent = { Text(session.title) }, supportingContent = { Text("${session.source} · ${session.state}") }, leadingContent = { Icon(if (session.pinned) Icons.Default.PushPin else Icons.Default.ChatBubbleOutline, null) }, modifier = Modifier.fillMaxWidth(), trailingContent = { IconButton(onClick = { selected = session }) { Icon(Icons.Default.ChevronRight, "Open") } }); HorizontalDivider() } }
                VerticalDivider()
                SessionPane(selected, history, prompt, { prompt = it }, pendingAttachments, attachmentBusy, attachmentStatus, onAttach = { session -> attachmentTargetSessionId = session.id; attachmentStatus = null; attachmentPicker.launch(arrayOf("*/*")) }, onCancelAttachment = { attachmentUploadJob?.cancel() }, onRemoveAttachment = { attachmentId -> pendingAttachments = pendingAttachments.filterNot { it.attachmentId == attachmentId } }, onLoadOlder = loadOlderHistory, onSend = { text -> selected?.let { session -> scope.launch { runCatching { client.action(AttachmentPromptPolicy.promptAction(credentials!!.deviceId, session.id, session.state == "busy", text, pendingAttachments, UUID.randomUUID().toString(), Instant.now().toString())) }.onSuccess { prompt = ""; pendingAttachments = emptyList(); attachmentStatus = null }.onFailure { error = it.message } } } }, onFork = { selected?.let { session -> scope.launch { client.action(AgentAction("forkSession", credentials!!.deviceId, UUID.randomUUID().toString(), session.id, createdAt = Instant.now().toString())); snapshot = client.snapshot() } } }, onRename = { title -> selected?.let { session -> scope.launch { runCatching { client.action(AgentAction("renameSession", credentials!!.deviceId, UUID.randomUUID().toString(), session.id, createdAt = Instant.now().toString(), payload = mapOf("title" to title))) }.onSuccess { snapshot = client.snapshot(); selected = snapshot?.sessions?.firstOrNull { it.id == session.id } }.onFailure { error = it.message } } } }, onPin = { selected?.let { session -> scope.launch { client.action(AgentAction(if (session.pinned) "unpinSession" else "pinSession", credentials!!.deviceId, UUID.randomUUID().toString(), session.id, createdAt = Instant.now().toString())); snapshot = client.snapshot() } } }, onSpeak = { speechConfirmation = true }, speaking = speaking, activeRun = selected?.let { RunControlPolicy.activeRunFor(it.id, snapshot?.activeRuns.orEmpty()) }, runControlEnabled = snapshot?.hermes?.sessionRunControl == true, onStop = { run -> scope.launch { runCatching { client.action(RunControlPolicy.stopAction(credentials!!.deviceId, run, UUID.randomUUID().toString(), Instant.now().toString())) }.onSuccess { snapshot = client.snapshot(); error = null }.onFailure { error = it.message } } }, modifier = Modifier.weight(1f))
            }
        }
    }

    private fun startConnection() = ContextCompat.startForegroundService(this, Intent(this, HermesConnectionService::class.java))
    @Composable private fun Pairing(onPaired: (DeviceCredentials) -> Unit) { var origin by remember { mutableStateOf(BuildConfig.DEFAULT_BRIDGE_ORIGIN) }; var code by remember { mutableStateOf("") }; var error by remember { mutableStateOf<String?>(null) }; var busy by remember { mutableStateOf(false) }; val scope = rememberCoroutineScope(); Column(Modifier.fillMaxSize().padding(32.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) { Text("Pair private bridge", style = MaterialTheme.typography.headlineLarge); Text("Enter the 90-second, single-use code shown by `hermes-g2-bridge pair android`. A revocable Android credential is generated and encrypted in Keystore; the Hermes master key never leaves the Mac mini."); OutlinedTextField(origin, { origin = it.trimEnd('/') }, label = { Text("Tailscale HTTPS origin") }, modifier = Modifier.fillMaxWidth()); OutlinedTextField(code, { code = it.filter(Char::isDigit).take(6) }, label = { Text("Pairing code") }, modifier = Modifier.fillMaxWidth()); error?.let { Text(it, color = MaterialTheme.colorScheme.error) }; Button(enabled = !busy && origin.startsWith("https://") && code.length == 6, onClick = { busy = true; scope.launch { runCatching { BridgeClient.exchange(origin, code, Build.MODEL) }.onSuccess(onPaired).onFailure { error = it.message; busy = false } } }) { Text(if (busy) "Pairing…" else "Pair device") } } }
    @Composable private fun Readiness(value: RuntimeReadiness?, error: String?) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(ReadinessPolicy.headline(value, error), fontWeight = FontWeight.Bold, color = if (value?.coreReady == true) Color(0xFF3A7D44) else MaterialTheme.colorScheme.error)
            if (value == null && error == null) Text("Waiting for the private bridge…", style = MaterialTheme.typography.bodySmall)
            ReadinessPolicy.lines(value, error).filterNot { value == null && it.label != "Last error" }.forEach { line ->
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    Icon(
                        if (line.ready) Icons.Default.CheckCircle else Icons.Default.ErrorOutline,
                        contentDescription = if (line.ready) "Ready" else "Unavailable",
                        tint = if (line.ready) Color(0xFF3A7D44) else MaterialTheme.colorScheme.error,
                    )
                    Column {
                        Text(line.label, fontWeight = FontWeight.SemiBold)
                        Text(line.detail, style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
        }
    }
    @Composable private fun SessionPane(
        session: SessionSummary?,
        history: MessagePage?,
        prompt: String,
        onPrompt: (String) -> Unit,
        attachments: List<AttachmentUpload>,
        attachmentBusy: Boolean,
        attachmentStatus: String?,
        onAttach: (SessionSummary) -> Unit,
        onCancelAttachment: () -> Unit,
        onRemoveAttachment: (String) -> Unit,
        onLoadOlder: () -> Unit,
        onSend: (String) -> Unit,
        onFork: () -> Unit,
        onRename: (String) -> Unit,
        onPin: () -> Unit,
        onSpeak: () -> Unit,
        speaking: Boolean,
        activeRun: ActiveRun?,
        runControlEnabled: Boolean,
        onStop: (ActiveRun) -> Unit,
        modifier: Modifier = Modifier,
    ) {
        var renaming by remember(session?.id) { mutableStateOf(false) }
        var title by remember(session?.id) { mutableStateOf(session?.title.orEmpty()) }
        var confirmingStop by remember(session?.id, activeRun?.runId) { mutableStateOf(false) }
        if (renaming) AlertDialog(
            onDismissRequest = { renaming = false },
            title = { Text("Rename exact session") },
            text = { OutlinedTextField(title, { title = it.take(120) }, label = { Text("Session title") }) },
            confirmButton = { Button(enabled = title.isNotBlank(), onClick = { renaming = false; onRename(title.trim()) }) { Text("Rename") } },
            dismissButton = { TextButton(onClick = { renaming = false }) { Text("Cancel") } },
        )
        if (confirmingStop && activeRun != null && session != null) AlertDialog(
            onDismissRequest = { confirmingStop = false },
            title = { Text("Stop this exact run?") },
            text = { Text("${session.title} · session ${session.id.take(8)} · run ${activeRun.runId.take(8)}. Hermes will interrupt it without rerunning automatically.") },
            confirmButton = { Button(onClick = { confirmingStop = false; onStop(activeRun) }) { Text("Stop run") } },
            dismissButton = { TextButton(onClick = { confirmingStop = false }) { Text("Cancel") } },
        )
        Column(modifier.padding(24.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
            Text(session?.title ?: "Select a session", style = MaterialTheme.typography.headlineMedium)
            session?.let {
                Text("${it.model ?: "Default model"} · ${it.source} · ${if (it.executionReady) "Execution ready" else "UNBOUND"}")
                activeRun?.let { run -> Text("Active run ${run.runId.take(8)} · ${run.status}", color = MaterialTheme.colorScheme.primary) }
                Row {
                    TextButton(onClick = onPin) { Text(if (it.pinned) "Unpin from G2" else "Pin to G2") }
                    TextButton(onClick = onFork) { Text("Fork") }
                    TextButton(onClick = { title = it.title; renaming = true }) { Text("Rename") }
                    TextButton(onClick = onSpeak, enabled = history?.data?.any { message -> message.role == "assistant" && message.content.isNotBlank() } == true) { Text(if (speaking) "Stop speech" else "Speak") }
                    if (activeRun != null) TextButton(onClick = { confirmingStop = true }, enabled = runControlEnabled) { Text("Stop run") }
                }
                LazyColumn(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    if (history?.hasMore == true) item {
                        TextButton(onClick = onLoadOlder) { Text("Load older messages") }
                    }
                    items(history?.data.orEmpty().asReversed(), key = { message -> message.id }) { message ->
                        Column {
                            Text(message.role.uppercase(), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.primary)
                            Text(message.content.ifBlank { "(${message.toolName ?: "empty"})" })
                        }
                    }
                }
                if (attachments.isNotEmpty()) Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    attachments.forEach { attachment ->
                        InputChip(
                            selected = true,
                            onClick = { onRemoveAttachment(attachment.attachmentId) },
                            label = { Text("${attachment.name} · remove") },
                        )
                    }
                }
                attachmentStatus?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = if (attachmentBusy) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant) }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedTextField(prompt, onPrompt, label = { Text("Continue this exact session") }, modifier = Modifier.weight(1f))
                    IconButton(onClick = { onAttach(it) }, enabled = !attachmentBusy && attachments.size < AttachmentSelectionPolicy.MAX_ATTACHMENTS) { Icon(Icons.Default.AttachFile, "Attach files") }
                    if (attachmentBusy) IconButton(onClick = onCancelAttachment) { Icon(Icons.Default.Close, "Cancel attachment upload") }
                    IconButton(onClick = { onSend(prompt) }, enabled = !attachmentBusy && (prompt.isNotBlank() || attachments.isNotEmpty())) { Icon(Icons.AutoMirrored.Filled.Send, "Send") }
                }
            }
        }
    }
    @Composable private fun ApprovalPane(value: ApprovalRequest, enabled: Boolean, onChoice: (String) -> Unit) {
        var pendingChoice by remember(value.requestId) { mutableStateOf<String?>(null) }
        var confirmationStep by remember(value.requestId) { mutableIntStateOf(0) }
        val requiresSecondConfirmation = pendingChoice?.let { ApprovalPolicy.requiresSecondConfirmation(it, value) } == true
        pendingChoice?.let { choice ->
            AlertDialog(
                onDismissRequest = { pendingChoice = null; confirmationStep = 0 },
                title = { Text(if (confirmationStep == 0) "Confirm ${ApprovalPolicy.displayLabel(choice)}" else "Confirm persistent or sensitive access") },
                text = {
                    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text("Tool: ${value.tool}")
                        Text(value.command ?: value.rule ?: value.destination ?: "No additional context was supplied.")
                        Text("Session ${value.sessionId.take(8)} · run ${value.runId.take(8)}")
                        if (requiresSecondConfirmation) Text("This decision is persistent or Hermes marked the operation sensitive. A second deliberate confirmation is required.", color = MaterialTheme.colorScheme.error)
                    }
                },
                confirmButton = {
                    Button(onClick = {
                        if (requiresSecondConfirmation && confirmationStep == 0) confirmationStep = 1
                        else { pendingChoice = null; confirmationStep = 0; onChoice(choice) }
                }) { Text(if (requiresSecondConfirmation && confirmationStep == 0) "Review again" else "Submit ${ApprovalPolicy.displayLabel(choice)}") }
                },
                dismissButton = { TextButton(onClick = { pendingChoice = null; confirmationStep = 0 }) { Text("Cancel") } },
            )
        }
        Card(Modifier.padding(12.dp)) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Approval required", fontWeight = FontWeight.Bold)
                Text(value.tool)
                Text(value.command ?: value.destination ?: "Review this request privately.")
                if (!enabled) Text("This Hermes build cannot accept native session approvals.", color = MaterialTheme.colorScheme.error)
                else Row { value.choices.forEach { choice ->
                    val supported = ApprovalPolicy.actionKindFor(choice) != null
                    Button(
                        onClick = { pendingChoice = choice; confirmationStep = 0 },
                        enabled = supported,
                        modifier = Modifier.padding(end = 6.dp),
                    ) { Text(ApprovalPolicy.displayLabel(choice)) }
                } }
            }
        }
    }
    @Composable private fun AuxiliaryPane(title: String, content: String, modifier: Modifier = Modifier) { Column(modifier.fillMaxSize().padding(24.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) { Text(title, style = MaterialTheme.typography.headlineMedium); Text("Read-only capability view; actions only appear when Hermes advertises them."); LazyColumn(Modifier.fillMaxSize()) { item { Text(content.ifBlank { "Loading…" }, style = MaterialTheme.typography.bodySmall) } } } }
    @Composable private fun JobsPane(value: JobList?, fallback: String, onAction: (String, String) -> Unit, modifier: Modifier = Modifier) { Column(modifier.fillMaxSize().padding(24.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) { Text("Jobs", style = MaterialTheme.typography.headlineMedium); if (value == null) Text(fallback.ifBlank { "Loading…" }) else if (value.jobs.isEmpty()) Text("No Hermes jobs configured.") else LazyColumn(Modifier.fillMaxSize(), verticalArrangement = Arrangement.spacedBy(10.dp)) { items(value.jobs, key = { it.id }) { job -> Card(Modifier.fillMaxWidth()) { Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) { Text(job.name, fontWeight = FontWeight.Bold); Text(listOfNotNull(job.state, job.scheduleDisplay, job.nextRunAt?.let { "Next $it" }, job.lastStatus?.let { "Last $it" }).joinToString(" · ")); job.lastError?.takeIf { it.isNotBlank() }?.let { Text(it, color = MaterialTheme.colorScheme.error) }; Row { Button(onClick = { onAction("runJob", job.id) }) { Text("Run now") }; Spacer(Modifier.width(8.dp)); if (job.state == "paused" || !job.enabled) OutlinedButton(onClick = { onAction("resumeJob", job.id) }) { Text("Resume") } else OutlinedButton(onClick = { onAction("pauseJob", job.id) }) { Text("Pause") } } } } } } } }
    @Composable private fun SkillsPane(value: SkillsInventory?, fallback: String, modifier: Modifier = Modifier) {
        var query by remember { mutableStateOf("") }
        val needle = query.trim().lowercase()
        val skills = value?.skills?.data.orEmpty().filter { skill ->
            needle.isBlank() || listOf(skill.name, skill.description, skill.category.orEmpty()).any { it.lowercase().contains(needle) }
        }
        val toolsets = value?.toolsets?.data.orEmpty().filter { toolset ->
            needle.isBlank() || listOf(toolset.name, toolset.label, toolset.description).any { it.lowercase().contains(needle) } || toolset.tools.any { it.lowercase().contains(needle) }
        }
        Column(modifier.fillMaxSize().padding(24.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text("Skills and toolsets", style = MaterialTheme.typography.headlineMedium)
            if (value == null) Text(fallback.ifBlank { "Loading…" }) else {
                Text("${value.skills.data.size} skills · ${value.toolsets.data.size} toolsets · read-only inventory")
                OutlinedTextField(query, { query = it }, label = { Text("Search capabilities") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                LazyColumn(Modifier.fillMaxSize(), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (toolsets.isNotEmpty()) item { Text("TOOLSETS", style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.primary) }
                    items(toolsets, key = { "toolset:${it.name}" }) { toolset ->
                        ListItem(
                            headlineContent = { Text(toolset.label) },
                            supportingContent = { Text("${if (toolset.enabled) "Enabled" else "Disabled"} · ${if (toolset.configured) "Configured" else "Needs setup"} · ${toolset.tools.size} tools\n${toolset.description}") },
                        )
                        HorizontalDivider()
                    }
                    if (skills.isNotEmpty()) item { Text("SKILLS", style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.primary) }
                    items(skills, key = { "skill:${it.name}" }) { skill ->
                        ListItem(
                            headlineContent = { Text(skill.name) },
                            supportingContent = { Text(listOfNotNull(skill.category, skill.description.takeIf { it.isNotBlank() }).joinToString(" · ")) },
                        )
                        HorizontalDivider()
                    }
                    if (skills.isEmpty() && toolsets.isEmpty()) item { Text("No capabilities match ‘$query’.") }
                }
            }
        }
    }
    @Composable private fun SecurityPane(value: List<DeviceRecord>?, ownDeviceId: String, audit: String, onRevoke: (String) -> Unit, modifier: Modifier = Modifier) { var pendingRevoke by remember { mutableStateOf<DeviceRecord?>(null) }; pendingRevoke?.let { device -> AlertDialog(onDismissRequest = { pendingRevoke = null }, title = { Text("Revoke ${device.name}?") }, text = { Text("This immediately disconnects that ${device.kind} credential. It must be paired again to regain access.") }, confirmButton = { Button(onClick = { pendingRevoke = null; onRevoke(device.id) }) { Text("Revoke") } }, dismissButton = { TextButton(onClick = { pendingRevoke = null }) { Text("Cancel") } }) }; Column(modifier.fillMaxSize().padding(24.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) { Text("Security", style = MaterialTheme.typography.headlineMedium); Text("Device credentials are independently scoped and revocable. This controller cannot revoke itself accidentally."); LazyColumn(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(8.dp)) { items(value.orEmpty(), key = { it.id }) { device -> ListItem(headlineContent = { Text(device.name) }, supportingContent = { Text("${device.kind} · ${if (device.revokedAt == null) "active" else "revoked"} · cursor ${device.acknowledgedCursor}\n${device.scopes.joinToString()}") }, trailingContent = { if (device.id == ownDeviceId) Text("THIS DEVICE") else if (device.revokedAt == null) TextButton(onClick = { pendingRevoke = device }) { Text("Revoke") } }); HorizontalDivider() }; item { Text("Recent audit", fontWeight = FontWeight.Bold); Text(audit.ifBlank { "No audit entries." }, style = MaterialTheme.typography.bodySmall) } } } }
    @OptIn(ExperimentalMaterial3Api::class)
    @Composable private fun ModelPane(value: ModelOptions?, session: SessionSummary?, onSelect: (String, String) -> Unit, modifier: Modifier = Modifier) { var provider by remember(value) { mutableStateOf(value?.provider.orEmpty()) }; var model by remember(value) { mutableStateOf(value?.model.orEmpty()) }; var providerExpanded by remember { mutableStateOf(false) }; var modelExpanded by remember { mutableStateOf(false) }; val availableProviders = value?.providers.orEmpty().filter { it.authenticated && it.models.isNotEmpty() }; val selectedProvider = availableProviders.firstOrNull { it.slug == provider }; Column(modifier.fillMaxSize().padding(24.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) { Text("Session model", style = MaterialTheme.typography.headlineMedium); Text(session?.let { "Target: ${it.title} · ${it.id.take(8)}" } ?: "Select a session first."); ExposedDropdownMenuBox(providerExpanded, { providerExpanded = !providerExpanded }) { OutlinedTextField(provider, {}, readOnly = true, label = { Text("Provider") }, trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(providerExpanded) }, modifier = Modifier.menuAnchor(MenuAnchorType.PrimaryNotEditable).fillMaxWidth()); ExposedDropdownMenu(providerExpanded, { providerExpanded = false }) { availableProviders.forEach { item -> DropdownMenuItem({ Text(item.name) }, { provider = item.slug; model = item.models.firstOrNull().orEmpty(); providerExpanded = false }) } } }; ExposedDropdownMenuBox(modelExpanded, { modelExpanded = !modelExpanded }) { OutlinedTextField(model, {}, readOnly = true, label = { Text("Model") }, trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(modelExpanded) }, modifier = Modifier.menuAnchor(MenuAnchorType.PrimaryNotEditable).fillMaxWidth()); ExposedDropdownMenu(modelExpanded, { modelExpanded = false }) { selectedProvider?.models.orEmpty().forEach { item -> DropdownMenuItem({ Text(item) }, { model = item; modelExpanded = false }) } } }; Button(enabled = session != null && provider.isNotBlank() && model.isNotBlank(), onClick = { onSelect(provider, model) }) { Text("Lock model to this session") }; Text("The selection is persisted by Hermes for this exact native session. Other Desktop, Telegram, CLI and G2 sessions are unchanged.") } }
    @Composable private fun HermesTheme(content: @Composable () -> Unit) { MaterialTheme(colorScheme = darkColorScheme(primary = Color(0xFF77E27B), background = Color(0xFF071008), surface = Color(0xFF0D180E)), content = content) }
}
