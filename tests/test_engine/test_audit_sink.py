"""Tests for SecurityAuditEmitter and AuditSink (engine/audit_sink.py)."""

from __future__ import annotations

from hecate.engine.audit_sink import (
    AuditSink,
    NullAuditSink,
    SecurityAuditEmitter,
    audit_emitter,
)


class TestSecurityAuditEmitter:
    def test_disabled_emitter_is_noop(self):
        emitter = SecurityAuditEmitter()
        assert not emitter.enabled
        emitter.emit({"tool_name": "bash", "decision": "allow"})

    def test_enabled_emitter_delegates_to_sink(self):
        collected: list[dict] = []

        class CollectingSink(AuditSink):
            def emit(self, event: dict) -> None:
                collected.append(event)

        emitter = SecurityAuditEmitter()
        emitter.set_sink(CollectingSink())
        assert emitter.enabled

        event = emitter.build_event(
            agent_id="agent-1",
            workspace_id="ws-1",
            tool_name="bash",
            decision="allow",
        )
        emitter.emit(event)
        assert len(collected) == 1
        assert collected[0]["tool_name"] == "bash"

    def test_disable_after_enable(self):
        emitter = SecurityAuditEmitter()
        emitter.set_sink(NullAuditSink())
        assert emitter.enabled
        emitter.disable()
        assert not emitter.enabled

    def test_emit_swallows_sink_exceptions(self):
        class FailingSink(AuditSink):
            def emit(self, event: dict) -> None:
                raise RuntimeError("sink failure")

        emitter = SecurityAuditEmitter()
        emitter.set_sink(FailingSink())
        emitter.emit({"tool_name": "test"})
        # Should not raise

    def test_build_event_includes_all_fields(self):
        emitter = SecurityAuditEmitter()
        event = emitter.build_event(
            agent_id="a1",
            workspace_id="w1",
            tool_name="bash",
            decision="deny",
            reason="dangerous pattern",
            arguments_hash="abc123",
            policy_version="v1",
            session_id="s1",
            on_behalf_of_user="user1",
            layer_results=[{"layer": "L1", "decision": "deny"}],
        )
        assert event["agent_id"] == "a1"
        assert event["workspace_id"] == "w1"
        assert event["tool_name"] == "bash"
        assert event["decision"] == "deny"
        assert event["reason"] == "dangerous pattern"
        assert event["arguments_hash"] == "abc123"
        assert event["session_id"] == "s1"
        assert event["on_behalf_of_user"] == "user1"
        assert len(event["layer_results"]) == 1

    def test_build_event_defaults(self):
        emitter = SecurityAuditEmitter()
        event = emitter.build_event(
            agent_id=None,
            workspace_id=None,
            tool_name="test",
            decision="allow",
        )
        assert event["agent_id"] == ""
        assert event["workspace_id"] == ""
        assert event["session_id"] is None
        assert event["layer_results"] == []

    def test_module_level_singleton_exists(self):
        assert audit_emitter is not None
        assert isinstance(audit_emitter, SecurityAuditEmitter)
