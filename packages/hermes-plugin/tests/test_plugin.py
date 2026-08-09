import inspect

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


def test_hook_event_families_are_durable_attention_events():
    assert HermesG2Observer._event_kind("pre_approval_request") == "attention.created"
    assert HermesG2Observer._event_kind("post_approval_response") == "attention.resolved"
    assert HermesG2Observer._event_kind("subagent_start") == "subagent.started"
    assert HermesG2Observer._event_kind("pre_llm_call") == "run.started"
    assert HermesG2Observer._event_kind("post_llm_call") == "run.completed"
    assert HermesG2Observer._event_kind("on_session_finalize") == "message.completed"
