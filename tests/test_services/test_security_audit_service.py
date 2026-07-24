"""Tests for SecurityAuditService batch writer and query."""

from __future__ import annotations

from hecate.models.security_audit import SecurityAuditQuerySchema
from hecate.services.security.audit_service import SecurityAuditService


class TestSecurityAuditServiceEmit:
    async def test_emit_buffers_event(self):
        svc = SecurityAuditService(batch_size=100, flush_interval=999)
        svc.emit(
            {
                "agent_id": "a1",
                "workspace_id": "w1",
                "tool_name": "bash",
                "decision": "allow",
                "reason": "test",
                "arguments_hash": "abc",
                "policy_version": "v1",
                "layer_results": [],
            }
        )
        assert not svc._queue.empty()

    async def test_emit_drops_on_queue_full(self):
        svc = SecurityAuditService(batch_size=1, flush_interval=999)
        svc._queue = svc._queue.__class__(maxsize=1)
        svc.emit({"agent_id": "a1", "workspace_id": "w1", "tool_name": "t", "decision": "d"})
        svc.emit({"agent_id": "a2", "workspace_id": "w2", "tool_name": "t", "decision": "d"})
        assert svc._queue.qsize() == 1


class TestSecurityAuditServiceQuery:
    async def test_query_returns_empty_when_no_data(self, db_session):
        svc = SecurityAuditService()
        params = SecurityAuditQuerySchema(agent_id="nonexistent")
        events, total = await svc.query(params, session=db_session)
        assert events == []
        assert total == 0

    async def test_query_returns_events(self, db_session):
        from hecate.models.security_audit import SecurityAuditModel

        row = SecurityAuditModel(
            agent_id="agent-test",
            workspace_id="ws-test",
            tool_name="bash",
            arguments_hash="abc123",
            decision="allow",
            reason="test reason",
            policy_version="v1",
            layer_results=[{"layer": "L1", "decision": "allow"}],
        )
        db_session.add(row)
        await db_session.flush()

        svc = SecurityAuditService()
        params = SecurityAuditQuerySchema(agent_id="agent-test")
        events, total = await svc.query(params, session=db_session)
        assert total == 1
        assert len(events) == 1
        assert events[0].agent_id == "agent-test"
        assert events[0].tool_name == "bash"
        assert events[0].decision == "allow"

    async def test_query_filters_by_decision(self, db_session):
        from hecate.models.security_audit import SecurityAuditModel

        for i in range(3):
            db_session.add(
                SecurityAuditModel(
                    agent_id=f"agent-{i}",
                    workspace_id="ws-test",
                    tool_name="bash",
                    arguments_hash="abc",
                    decision="deny" if i == 0 else "allow",
                    reason="",
                    policy_version="v1",
                    layer_results=[],
                )
            )
        await db_session.flush()

        svc = SecurityAuditService()
        params = SecurityAuditQuerySchema(decision="deny")
        events, total = await svc.query(params, session=db_session)
        assert total == 1
        assert events[0].decision == "deny"

    async def test_query_pagination(self, db_session):
        from hecate.models.security_audit import SecurityAuditModel

        for i in range(5):
            db_session.add(
                SecurityAuditModel(
                    agent_id=f"agent-{i}",
                    workspace_id="ws-test",
                    tool_name="bash",
                    arguments_hash="abc",
                    decision="allow",
                    reason="",
                    policy_version="v1",
                    layer_results=[],
                )
            )
        await db_session.flush()

        svc = SecurityAuditService()
        params = SecurityAuditQuerySchema(limit=2, offset=0)
        events, total = await svc.query(params, session=db_session)
        assert total == 5
        assert len(events) == 2
