"""Dispatch-integration and local approval authority tests (tasks 5.2, 5.4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest_runner import write_profile
from hecate_durable.storage.store import SqlDurableStore
from hecate_governance.approval import ActionRequest
from hecate_runner.engine import ExecutionEngine, RunState
from hecate_runner.evidence import EvidenceStore
from hecate_runner.governance import LocalPolicyProfile, LocalRevocationView
from hecate_runner.local_approvals import (
    DurableLocalApprovalLedger,
    make_local_approval_gate,
)
from hecate_runner.profile import load_profile


@pytest.fixture(autouse=True)
def _shutdown_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNNER_SHUTDOWN_TOKEN", "shutdown")


def _engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, with_write: bool = True):
    monkeypatch.setenv("RUNNER_SHUTDOWN_TOKEN", "shutdown")
    profile = load_profile(
        write_profile(
            tmp_path,
            durable={"database_url": f"sqlite:///{tmp_path / 'g.db'}", "workspace": "ws"},
            with_write_tool=with_write,
        )
    )
    store = SqlDurableStore(f"sqlite:///{tmp_path / 'g.db'}")
    store.create_schema()
    from hecate_durable.storage.models import Base

    with store.session_factory():
        Base.metadata.create_all(store.engine)
    evidence = EvidenceStore(profile.config.evidence_dir)
    calls: list = []

    async def dispatch(tool_name, arguments, principal, domains):  # noqa: ANN001
        calls.append((tool_name, arguments))
        return {"status": "ok", "result": {"done": True}}

    return profile, ExecutionEngine(profile, evidence, dispatch), calls, store


def _state(run_id: str = "r-g1") -> RunState:
    return RunState(run_id=run_id, status="running", principal="app-writer", domains=("domain_a",))


class TestDispatchIntegration:
    async def test_denied_write_zero_business_calls(self, tmp_path, monkeypatch) -> None:
        profile, engine, calls, _ = _engine(tmp_path, monkeypatch)
        # No governance rules configured → default deny.
        from hecate_governance.policy import GovernanceService

        engine._governance_service = GovernanceService(LocalPolicyProfile(profile.governance))
        state = _state()
        outcome = await engine._dispatch_tool(
            state, "submit_inventory_update", {"domain": "domain_a", "sku": "s", "quantity": 1}
        )
        assert outcome["status"] == "authorization"
        assert calls == []  # zero business side effects
        assert state.dispatch_denied is True

    async def test_revocation_blocks_readonly_dispatch(self, tmp_path, monkeypatch) -> None:
        profile, engine, calls, _ = _engine(tmp_path, monkeypatch, with_write=False)
        from hecate_governance.policy import GovernanceService

        config = type(profile.governance)(
            rules=({"tool": "query_inventory", "domains": ("domain_a",), "approval_required": False},),
            revoked_principals=("app-writer",),
        )
        engine._governance_service = GovernanceService(
            LocalPolicyProfile(config), revocation=LocalRevocationView(config)
        )
        state = _state()
        outcome = await engine._dispatch_tool(state, "query_inventory", {"domain": "domain_a", "sku": "s"})
        assert outcome["status"] == "authorization"
        assert calls == []  # readonly is NOT exempt from authorization
        denials = engine._evidence.query(kind="policy_denial")
        assert denials and denials[-1].detail["tool"] == "query_inventory"

    async def test_allowed_readonly_passes_through(self, tmp_path, monkeypatch) -> None:
        profile, engine, calls, _ = _engine(tmp_path, monkeypatch, with_write=False)
        from hecate_governance.policy import GovernanceService

        config = type(profile.governance)(
            rules=({"tool": "query_inventory", "domains": ("domain_a",), "approval_required": False},),
        )
        engine._governance_service = GovernanceService(LocalPolicyProfile(config))
        state = _state()
        outcome = await engine._dispatch_tool(state, "query_inventory", {"domain": "domain_a", "sku": "s"})
        assert outcome["status"] == "ok"
        assert len(calls) == 1

    async def test_no_service_keeps_step6_semantics(self, tmp_path, monkeypatch) -> None:
        _profile, engine, calls, _ = _engine(tmp_path, monkeypatch, with_write=False)
        state = _state()
        outcome = await engine._dispatch_tool(state, "query_inventory", {"domain": "domain_a", "sku": "s"})
        assert outcome["status"] == "ok"
        assert len(calls) == 1


def _request(invocation_id="action-1", digest="d1", fingerprint="fp-1"):
    return ActionRequest(
        invocation_id=invocation_id,
        tool_name="submit_inventory_update",
        arguments_digest=digest,
        target_resources=(),
        principal_fingerprint=fingerprint,
    )


class TestLocalApprovalAuthority:
    def _ledger(self, tmp_path: Path) -> tuple[DurableLocalApprovalLedger, SqlDurableStore]:
        store = SqlDurableStore(f"sqlite:///{tmp_path / 'approvals.db'}")
        store.create_schema()
        from hecate_durable.storage.models import Base

        with store.session_factory():
            Base.metadata.create_all(store.engine)
        return DurableLocalApprovalLedger(store.session_factory), store

    def _binding(self, ledger: DurableLocalApprovalLedger, ref: str, approver: str = "approver-1"):
        from hecate_governance.approval import ApprovalBinding

        ledger._record_binding(
            ApprovalBinding(
                approval_ref=ref,
                principal_fingerprint="fp-1",
                tool_name="submit_inventory_update",
                arguments_digest="d1",
                issued_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(minutes=30),
                one_time=True,
                judgment="approved",
                decided_by=approver,
            )
        )

    async def test_restart_persistence_second_consumption_refused(self, tmp_path: Path) -> None:
        ledger, _store = self._ledger(tmp_path)
        self._binding(ledger, "appr-x")
        from hecate_governance.approval import ActionRequest

        request = ActionRequest(
            invocation_id="tool:submit_inventory_update:d1",
            tool_name="submit_inventory_update",
            arguments_digest="d1",
            target_resources=(),
            principal_fingerprint="fp-1",
        )
        first = await ledger.consume("appr-x", request, now=datetime.now(UTC))
        assert first.status.value == "consumed"
        # A brand-new ledger instance over the same storage: the consumption
        # survives the restart.
        ledger2, _ = self._ledger(tmp_path)
        second = await ledger2.consume("appr-x", request, now=datetime.now(UTC))
        assert second.status.value == "same_action_rebound"
        other = await ledger2.consume(
            "appr-x",
            ActionRequest(
                invocation_id="tool:submit_inventory_update:OTHER",
                tool_name="submit_inventory_update",
                arguments_digest="d1",
                target_resources=(),
                principal_fingerprint="fp-1",
            ),
            now=datetime.now(UTC),
        )
        assert other.status.value == "conflict"

    async def test_local_revocation_rejects_binding(self, tmp_path: Path) -> None:
        ledger, _ = self._ledger(tmp_path)
        self._binding(ledger, "appr-r")
        await ledger.revoke("appr-r")
        from hecate_governance.approval import ActionRequest

        outcome = await ledger.consume(
            "appr-r",
            ActionRequest(
                invocation_id="tool:a:d1",
                tool_name="submit_inventory_update",
                arguments_digest="d1",
                target_resources=(),
                principal_fingerprint="fp-1",
            ),
            now=datetime.now(UTC),
        )
        assert outcome.status.value == "revoked"

    async def test_unregistered_approver_invalid(self, tmp_path: Path) -> None:
        ledger, store = self._ledger(tmp_path)
        self._binding(ledger, "appr-u", approver="mallory")
        gate = make_local_approval_gate(store.session_factory, approvers=("approver-1",))
        assert await gate("appr-u", _request()) is False

    async def test_registered_approver_gate_consumes_once(self, tmp_path: Path) -> None:
        ledger, store = self._ledger(tmp_path)
        self._binding(ledger, "appr-ok", approver="approver-1")
        gate = make_local_approval_gate(store.session_factory, approvers=("approver-1",))
        assert await gate("appr-ok", _request()) is True
        # The same action recovering re-enters on its consumption association.
        assert await gate("appr-ok", _request()) is True
        # A DIFFERENT action loses: the one-time approval is spent.
        assert await gate("appr-ok", _request(invocation_id="action-2")) is False

    async def test_gate_with_trusted_time_rejects_after_rollback(self, tmp_path: Path) -> None:
        ledger, store = self._ledger(tmp_path)
        self._binding(ledger, "appr-t", approver="approver-1")
        from hecate_governance.clock import TimeAnchor, TrustedClock

        clock = TrustedClock(now_fn=lambda: datetime.now(UTC) - timedelta(hours=2))
        future_anchor = TimeAnchor(wall=datetime.now(UTC), monotonic=0.0)
        gate = make_local_approval_gate(
            store.session_factory, approvers=("approver-1",), clock=clock, anchor=future_anchor
        )
        # The clock sits two hours BEFORE the anchor → rollback detected →
        # the expiry-dependent binding is refused.
        assert await gate("appr-t", _request()) is False
