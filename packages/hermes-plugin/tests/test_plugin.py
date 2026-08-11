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
