"""Local authority regressions for the standalone production assembly."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from conftest_runner import write_profile
from hecate_durable.storage import SqlDurableStore
from hecate_governance.action import target_resources_for, tool_arguments_digest
from hecate_governance.approval import ApprovalBinding, MemoryApprovalLedger
from hecate_governance.policy import GovernanceService
from hecate_governance.principal import ActionPrincipal, PrincipalSource
from hecate_governance.usage import ReservationStatus, UsageRecord, UsageStatus
from hecate_runner.budget import DurableBudgetLedger
from hecate_runner.engine import ExecutionEngine, RunState
from hecate_runner.evidence import EvidenceStore
from hecate_runner.governance import LocalPolicyProfile, LocalRevocationView
from hecate_runner.profile import GovernanceLocalConfig, load_profile


async def test_independent_local_budget_ledgers_serialize_capacity(tmp_path):
    store = SqlDurableStore(f"sqlite:///{tmp_path / 'budget.db'}")
    store.create_schema()
    first = DurableBudgetLedger(store.session_factory, limit=100)
    second = DurableBudgetLedger(store.session_factory, limit=100)
    outcomes = await asyncio.gather(
        asyncio.to_thread(first._sync_reserve, "a", 80), asyncio.to_thread(second._sync_reserve, "b", 80)
    )
    assert sum(outcome.status is ReservationStatus.RESERVED for outcome in outcomes) == 1
    key = "a" if outcomes[0].status is ReservationStatus.RESERVED else "b"
    await first.settle(UsageRecord(key, 0, UsageStatus.UNKNOWN, "cancelled"))
    assert await second.remaining() == 20
    assert (await second.usage_for(key)).status is UsageStatus.UNKNOWN
    await second.settle(UsageRecord(key, 30, UsageStatus.ESTIMATED, "reconciled"))
    assert await first.remaining() == 70
    assert (await first.usage_for(key)).status is UsageStatus.ESTIMATED
    store.dispose()


async def test_required_approval_parks_and_uses_actual_invocation(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNNER_SHUTDOWN_TOKEN", "test")
    profile = load_profile(write_profile(tmp_path))
    config = GovernanceLocalConfig(
        rules=({"tool": "query_inventory", "domains": ("domain_a",), "approval_required": True},)
    )
    authority = MemoryApprovalLedger()
    calls = []

    async def dispatch(*args):
        calls.append(args)
        return {"status": "ok", "result": {}}

    engine = ExecutionEngine(
        profile,
        EvidenceStore(profile.config.evidence_dir),
        dispatch,
        governance_service=GovernanceService(LocalPolicyProfile(config), approval_ledger=authority),
    )
    arguments = {"domain": "domain_a", "sku": "s"}
    state = RunState("a", "running", "reader", ("domain_a",))
    outcome = await engine._dispatch_tool(state, "query_inventory", arguments)
    assert outcome["status"] == "awaiting_approval"
    assert state.wait_request is not None
    assert not state.dispatch_denied
    assert calls == []
    principal = ActionPrincipal.from_verified_claims(
        {"sub": "reader"}, source=PrincipalSource.RUNNER_LOCAL, domains=("domain_a",)
    )
    await authority.save(
        ApprovalBinding(
            approval_ref="grant",
            principal_fingerprint=principal.fingerprint(),
            tool_name="query_inventory",
            arguments_digest=tool_arguments_digest(arguments),
            target_resources=target_resources_for(arguments),
            domains=("domain_a",),
            issued_at=datetime.now(UTC),
            judgment="approved",
            one_time=True,
        )
    )
    # The service consumes only after the policy has accepted the actual domain.
    current = GovernanceLocalConfig(
        rules=({"tool": "query_inventory", "domains": ("domain_a",), "approval_required": False},)
    )
    revocation = LocalRevocationView(lambda: current)
    service = GovernanceService(LocalPolicyProfile(lambda: current), revocation=revocation, approval_ledger=authority)
    engine._governance_service = service
    wrong = RunState("b", "running", "reader", ("domain_a", "domain_b"))
    assert (await engine._dispatch_tool(wrong, "query_inventory", {"domain": "domain_b", "sku": "s"}))[
        "status"
    ] == "authorization"
    assert calls == []
    current = GovernanceLocalConfig(rules=current.rules, revoked_principals=("reader",))
    revoked = RunState("c", "running", "reader", ("domain_a",))
    assert (await engine._dispatch_tool(revoked, "query_inventory", arguments))["status"] == "authorization"
    assert (await authority.get("grant")).consumed_by_invocation_id is None
