package com.honey.hermesg2

import com.honey.hermesg2.data.RuntimeReadiness

data class ReadinessLine(val label: String, val ready: Boolean, val detail: String)

object ReadinessPolicy {
    fun lines(value: RuntimeReadiness?, error: String? = null): List<ReadinessLine> {
        val runtime = value ?: RuntimeReadiness()
        return buildList {
            add(ReadinessLine("Bridge", runtime.bridge, if (runtime.bridge) "Private bridge reachable" else "Waiting for the private bridge"))
            add(ReadinessLine("Hermes", runtime.hermes, if (runtime.hermes) "API server authenticated" else "Hermes API unavailable"))
            add(ReadinessLine("Core", runtime.coreReady, runtime.reason ?: if (runtime.coreReady) "Chat and memory available" else "Core services are not ready"))
            add(ReadinessLine("Tailscale", runtime.tailscale, if (runtime.tailscale) "Private route available" else "Join the configured tailnet"))
            add(ReadinessLine("GUI tools", runtime.guiReady, if (runtime.guiReady) "Desktop-only tools available" else "Unavailable while the Mac mini is logged out"))
            add(ReadinessLine("Speech-to-text", runtime.stt, if (runtime.stt) "Local transcription ready" else "Local Whisper is unavailable"))
            add(ReadinessLine("Summaries", runtime.summary, if (runtime.summary) "Response condensation ready" else "Deterministic summaries will be used"))
            error?.takeIf { it.isNotBlank() }?.let { add(ReadinessLine("Last error", false, it)) }
        }
    }

    fun headline(value: RuntimeReadiness?, error: String? = null): String = when {
        error?.isNotBlank() == true -> "Action needs attention"
        value?.coreReady == true -> "Core ready"
        value == null -> "Connecting privately…"
        else -> "Core unavailable"
    }
}
