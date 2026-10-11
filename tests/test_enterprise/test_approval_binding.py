"""Authority-backed chat approval callback tests (task 3.2)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from hecate_governance.approval import ActionRequest, MemoryApprovalLedger

from hecate.enterprise.approval_binding import (
    AuthorityBackedApprovalCallback,
    chat_subject_fingerprint,
)
from hecate.runtime.security.approval import approval_arguments_digest
from hecate.runtime.tool_access import ApprovalDecision, ApprovalScope


def _ref(tool_call_id: str) -> str:
    """Approval ref for the fixed test session (uuid int=1)."""
    return f"chat:{uuid.UUID(int=1)}:{tool_call_id}"


def _event_store() -> MagicMock:
    store = MagicMock()
    store.append = AsyncMock()
    return store


def _callback(ledger: MemoryApprovalLedger) -> AuthorityBackedApprovalCallback:
    callback = AuthorityBackedApprovalCallback(
        authority=ledger,
        event_store=_event_store(),
        session_id=uuid.UUID(int=1),
        workspace_id=uuid.UUID(int=2),
        agent_id=uuid.UUID(int=3),
    )
    return callback


class _ApproveSession:
    async def request_approval(self, *, tool_name, arguments, risk_level, context):
        return ApprovalDecision(approved=True, reason="ok", scope=ApprovalScope.SESSION)


class TestAuthorityBindingCreation:
    async def test_approval_creates_durable_binding(self) -> None:
        ledger = MemoryApprovalLedger()
        callback = _callback(ledger)
        callback._inner_backend = _ApproveSession()
        decision = await callback.request_approval(
            tool_name="bash",
            arguments={"command": "ls"},
            risk_level="high",
            context={},
        )
        assert decision.approved is True
        binding = await ledger.get(decision.approval_ref)
        assert binding is not None
        assert binding.judgment.value == "approved"
        assert binding.arguments_digest == approval_arguments_digest({"command": "ls"})
        expected_subject = chat_subject_fingerprint(
            session_id=uuid.UUID(int=1),
            workspace_id=uuid.UUID(int=2),
            agent_id=uuid.UUID(int=3),
        )
        assert binding.principal_fingerprint == expected_subject
        assert binding.one_time is False  # SESSION scope

    async def test_binding_carries_expiry(self) -> None:
        ledger = MemoryApprovalLedger()
        callback = _callback(ledger)
        callback._inner_backend = _ApproveSession()
        expires = datetime.now(UTC) + timedelta(minutes=5)
        decision = await callback.request_approval(
            tool_name="bash",
            arguments={"tool_call_id": "tc-2"},
            risk_level="high",
            context={"approval_expires_at": expires},
        )
        binding = await ledger.get(decision.approval_ref)
        assert binding is not None
        assert binding.expires_at is not None
        assert abs((binding.expires_at - expires).total_seconds()) < 1

    async def test_denied_approval_creates_no_binding(self) -> None:
        class _Deny:
            async def request_approval(self, *, tool_name, arguments, risk_level, context):
                return ApprovalDecision(approved=False, reason="no", scope=ApprovalScope.ONCE)

        ledger = MemoryApprovalLedger()
        callback = _callback(ledger)
        callback._inner_backend = _Deny()
        decision = await callback.request_approval(
            tool_name="bash", arguments={"tool_call_id": "tc-3"}, risk_level="high", context={}
        )
        assert not decision.approved
        assert decision.approval_ref is None
        assert await ledger.get(_ref("tc-3")) is None


class TestCrossSubjectReuse:
    async def test_same_parameters_other_subject_refused(self) -> None:
        # Subject P1's approval is bound to P1's fingerprint; a dispatch by
        # another subject referencing the same approval is a mismatch.
        ledger = MemoryApprovalLedger()
        callback = _callback(ledger)
        callback._inner_backend = _ApproveSession()
        decision = await callback.request_approval(
            tool_name="bash",
            arguments={"command": "ls"},
            risk_level="high",
            context={},
        )
        binding = await ledger.get(decision.approval_ref)
        assert binding is not None
        other_subject = chat_subject_fingerprint(
            session_id=uuid.UUID(int=99),
            workspace_id=uuid.UUID(int=2),
            agent_id=uuid.UUID(int=99),
        )
        request = ActionRequest(
            invocation_id="tool-attacker",
            tool_name="bash",
            arguments_digest=approval_arguments_digest({"command": "ls"}),
            target_resources=(),
            principal_fingerprint=other_subject,
        )
        outcome = await ledger.consume(decision.approval_ref, request, now=datetime.now(UTC))
        assert outcome.status.value == "conflict"

    async def test_same_subject_binding_validates(self) -> None:
        ledger = MemoryApprovalLedger()
        callback = _callback(ledger)
        callback._inner_backend = _ApproveSession()
        decision = await callback.request_approval(
            tool_name="bash",
            arguments={"command": "ls"},
            risk_level="high",
            context={},
        )
        subject = chat_subject_fingerprint(
            session_id=uuid.UUID(int=1),
            workspace_id=uuid.UUID(int=2),
            agent_id=uuid.UUID(int=3),
        )
        request = ActionRequest(
            invocation_id="tool-owner",
            tool_name="bash",
            arguments_digest=approval_arguments_digest({"command": "ls"}),
            target_resources=(),
            principal_fingerprint=subject,
            task_scope=str(uuid.UUID(int=1)),
        )
        outcome = await ledger.consume(decision.approval_ref, request, now=datetime.now(UTC))
        assert outcome.status.value == "consumed"


async def test_cached_grant_keeps_authority_reference_in_audit_events() -> None:
    ledger = MemoryApprovalLedger()
    callback = _callback(ledger)
    callback._inner_backend = _ApproveSession()
    context = {"tool_call_id": "provider-handle" * 100}
    first = await callback.request_approval("bash", {"command": "ls"}, "high", context)
    second = await callback.request_approval("bash", {"command": "ls"}, "high", context)
    assert first.approval_ref is not None
    assert second.approval_ref == first.approval_ref
    assert len(first.approval_ref) <= 128
    decisions = [
        call.args[0]
        for call in callback.event_store.append.await_args_list
        if call.args[0].event_type.value == "APPROVAL_DECIDED"
    ]
    assert len(decisions) == 2
    assert all(event.payload["approval_ref"] == first.approval_ref for event in decisions)
