import inspect
import threading
import time

from hermes_g2_plugin import HermesG2Observer, register


class HookContext:
    def __init__(self):
        self.hooks = {}

    def register_hook(self, name, callback):
        self.hooks[name] = callback


def test_registers_installed_hermes_lifecycle_contract(monkeypatch):
    monkeypatch.setenv("HERMES_G2_PLUGIN_SECRET", "test-secret")
    context = HookContext()
    register(context)
    assert set(context.hooks) == {
        "on_session_start", "on_session_end", "on_session_finalize", "on_session_reset",
        "pre_tool_call", "post_tool_call", "pre_llm_call", "post_llm_call", "subagent_start",
        "subagent_stop", "pre_approval_request", "post_approval_response",
    }
    assert all(callable(callback) for callback in context.hooks.values())
    assert all(not inspect.iscoroutinefunction(callback) for callback in context.hooks.values())


def test_hook_event_families_never_compete_with_native_run_terminal_events():
    assert HermesG2Observer._event_kind("pre_approval_request") == "attention.created"
    assert HermesG2Observer._event_kind("post_approval_response") == "attention.resolved"
    assert HermesG2Observer._event_kind("subagent_start") == "subagent.started"
    assert HermesG2Observer._event_kind("pre_llm_call") == "run.progress"
    assert HermesG2Observer._event_kind("post_llm_call") == "run.progress"
    assert HermesG2Observer._event_kind("on_session_finalize") == "session.updated"
    assert HermesG2Observer._run_id({"turn_id": "session-like-id"}) is None
    assert HermesG2Observer._run_id({"run_id": "run-authoritative"}) == "run-authoritative"


def test_hook_delivery_never_waits_for_bridge_io(monkeypatch):
    monkeypatch.setenv("HERMES_G2_PLUGIN_SECRET", "test-secret")
    release = threading.Event()
    delivered = threading.Event()

    def blocked_sender(_envelope):
        delivered.set()
        release.wait(timeout=1)

    observer = HermesG2Observer(sender=blocked_sender)
    started = time.perf_counter()
    observer.pre_tool_call(session_id="session", run_id="run", tool_name="shell")
    elapsed = time.perf_counter() - started

    assert elapsed < 0.05
    assert delivered.wait(timeout=0.5)
    release.set()
    observer.close()


def test_queue_is_bounded_and_fail_open(monkeypatch):
    monkeypatch.setenv("HERMES_G2_PLUGIN_SECRET", "test-secret")
    release = threading.Event()

    def blocked_sender(_envelope):
        release.wait(timeout=1)

    observer = HermesG2Observer(sender=blocked_sender, queue_size=2)
    for index in range(20):
        observer.post_tool_call(session_id="session", run_id="run", tool_name=f"tool-{index}")

    assert observer.pending_count <= 2
    assert observer.dropped_count > 0
    release.set()
    observer.close()


def test_critical_approval_survives_a_full_progress_buffer(monkeypatch):
    monkeypatch.setenv("HERMES_G2_PLUGIN_SECRET", "test-secret")
    release = threading.Event()
    sender_started = threading.Event()
    delivered = []

    def blocked_sender(envelope):
        delivered.append(envelope)
        sender_started.set()
        release.wait(timeout=1)

    observer = HermesG2Observer(sender=blocked_sender, queue_size=3)
    observer.pre_llm_call(session_id="session", run_id="run", status="streaming")
    assert sender_started.wait(timeout=0.5)
    for index in range(3):
        observer.post_llm_call(
            session_id="session",
            run_id="run",
            status="streaming",
            tool_name=f"progress-{index}",
        )

    observer.pre_approval_request(
        session_id="session",
        run_id="run",
        tool_name="shell",
        choice="once",
    )
    assert observer.pending_count == 3
    assert observer.dropped_count >= 1

    release.set()
    observer.close()
    assert any(event["kind"] == "attention.created" for event in delivered)


def test_failure_and_terminal_lifecycle_events_are_preferred(monkeypatch):
    monkeypatch.setenv("HERMES_G2_PLUGIN_SECRET", "test-secret")
    release = threading.Event()
    sender_started = threading.Event()
    delivered = []

    def blocked_sender(envelope):
        delivered.append(envelope)
        sender_started.set()
        release.wait(timeout=1)

    observer = HermesG2Observer(sender=blocked_sender, queue_size=2)
    observer.pre_llm_call(session_id="session", run_id="run", status="streaming")
    assert sender_started.wait(timeout=0.5)
    observer.post_llm_call(session_id="session", run_id="run", status="streaming")
    observer.post_llm_call(session_id="session", run_id="run", status="streaming")
    observer.post_tool_call(
        session_id="session",
        run_id="run",
        status="failed",
        tool_name="shell",
    )
    observer.on_session_finalize(session_id="session", run_id="run", status="completed")

    assert observer.pending_count == 2
    release.set()
    observer.close()

    kinds = [event["kind"] for event in delivered]
    assert "tool.completed" in kinds
    assert "session.updated" in kinds
    assert delivered[-2]["payload"]["status"] in {"failed", "completed"}


def test_unknown_hook_events_are_retained_as_opaque_events(monkeypatch):
    monkeypatch.setenv("HERMES_G2_PLUGIN_SECRET", "test-secret")
    delivered = []
    observer = HermesG2Observer(sender=delivered.append)

    observer._send(
        "future_hermes_hook",
        {"session_id": "session", "status": "future"},
    )
    deadline = time.time() + 0.5
    while not delivered and time.time() < deadline:
        time.sleep(0.005)
    observer.close()

    assert HermesG2Observer._event_kind("future_hermes_hook") == "unknown"
    assert delivered[0]["kind"] == "unknown"
    assert delivered[0]["payload"]["hook"] == "future_hermes_hook"
    assert HermesG2Observer._event_priority(
        "future_hermes_hook",
        event_kind="unknown",
        kwargs={},
    ) == 20
