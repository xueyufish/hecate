"""Regression coverage for production governance boundaries and transactions."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from hecate_governance.action import ActionDecision, target_resources_for, tool_arguments_digest
from hecate_governance.approval import ActionRequest, ApprovalBinding, ApprovalGate, MemoryApprovalLedger
from hecate_governance.claims import ClaimStatus, MemoryActionClaimLedger, OutcomeUnknownError
from hecate_governance.policy import GovernanceService, PolicyOutcome, PolicyProfile, RevocationView
from hecate_governance.usage import ReservationStatus, UsageRecord, UsageStatus

from hecate.core.composition.governance import GovernanceDeniedError, PlatformGovernance
from hecate.core.composition.runtime_port_adapter import create_runtime_port
from hecate.enterprise.governance_ledgers import SqlActionClaimLedger
from hecate.models.quota import QuotaModel
from hecate.ops.budget_reservations import QuotaBudgetLedger


class StubProfile(PolicyProfile):
    version = "test"

    def __init__(self, decision=ActionDecision.ALLOW):
        self.decision = decision
        self.actors = []

    async def evaluate(self, context):
        self.actors.append(context.principal.actor)
        return PolicyOutcome(self.decision, self.version)


class StubRegistry:
    def __init__(self, result=42):
        self.calls = 0
        self.result = result
        self.metadata = {"name": "write", "parameters": {"type": "object", "required": ["domain"]}}

    async def describe(self, name):
        return self.metadata

    async def execute(self, name, args, context=None):
        self.calls += 1
        return self.result


def _claim_args():
    return dict(
        tool_name="write",
        arguments_digest="digest",
        principal_fingerprint="subject",
        side_effect_class="non_idempotent_write",
    )


async def test_committed_claim_survives_request_rollback(governance_session_factory):
    factory = governance_session_factory
    async with factory() as request:
        ledger = SqlActionClaimLedger(request, session_factory=factory)
        assert (await ledger.claim("action", **_claim_args())).status is ClaimStatus.CLAIMED
        await request.rollback()
        assert (await ledger.claim("action", **_claim_args())).status is ClaimStatus.DUPLICATE_IN_FLIGHT
        await ledger.complete("action", succeeded=True, result="scalar receipt")
        assert (await ledger.claim("action", **_claim_args())).record.result == "scalar receipt"


async def test_independent_claims_admit_one_executor(governance_session_factory):
    factory = governance_session_factory
    async with factory() as request:
        ledgers = [SqlActionClaimLedger(request, session_factory=factory) for _ in range(2)]
        outcomes = await asyncio.gather(*(ledger.claim("race", **_claim_args()) for ledger in ledgers))
        assert sum(outcome.status is ClaimStatus.CLAIMED for outcome in outcomes) == 1


async def test_stale_empty_read_cannot_claim_completed_action(governance_session_factory):
    factory = governance_session_factory
    async with factory() as request:
        ledger = SqlActionClaimLedger(request, session_factory=factory)
        await ledger.claim("race", **_claim_args())
        await ledger.complete("race", succeeded=True, result=99)

    class StaleLedger(SqlActionClaimLedger):
        stale = True

        async def _latest(self, key):
            if self.stale:
                self.stale = False
                return None
            return await super()._latest(key)

    async with factory.begin() as request:
        outcome = await StaleLedger(request).claim("race", **_claim_args())
        assert outcome.status is ClaimStatus.COMPLETED_REPLAY
        assert outcome.record.result == 99


async def test_concurrent_budget_capacity_and_unknown_hold(governance_session_factory):
    factory = governance_session_factory
    workspace = uuid.uuid4()
    async with factory.begin() as request:
        request.add(
            QuotaModel(
                name="limit",
                resource_type="cost",
                scope="workspace",
                scope_id=workspace,
                workspace_id=workspace,
                window_type="monthly",
                limit_value=100,
            )
        )
    async with factory() as request:
        ledger = QuotaBudgetLedger(request, workspace_id=workspace, session_factory=factory)
        outcomes = await asyncio.gather(ledger.reserve("a", 80), ledger.reserve("b", 80))
        assert sum(outcome.status is ReservationStatus.RESERVED for outcome in outcomes) == 1
        winner = "a" if outcomes[0].status is ReservationStatus.RESERVED else "b"
        await ledger.settle(UsageRecord(winner, 0, UsageStatus.UNKNOWN, "connection lost"))
        assert await ledger.remaining() == 20
        assert (await ledger.usage_for(winner)).status is UsageStatus.UNKNOWN
        await ledger.settle(UsageRecord(winner, 30, UsageStatus.ESTIMATED, "reconciled estimate"))
        assert await ledger.remaining() == 70
        assert (await ledger.usage_for(winner)).status is UsageStatus.ESTIMATED


async def test_factory_preserves_verified_actor_and_new_identical_actions(db_session):
    profile = StubProfile()
    registry = StubRegistry()
    port = create_runtime_port(db_session, None, registry, identity={"actor": "verified-user"})
    port._governance._service = GovernanceService(profile)
    assert await port.tool_execute("write", {"domain": "a"}, {"agent_id": "unrelated-agent"}) == 42
    assert await port.tool_execute("write", {"domain": "a"}, {"agent_id": "unrelated-agent"}) == 42
    assert registry.calls == 2
    assert profile.actors == ["verified-user", "verified-user"]


async def test_replay_precedes_revocation_and_external_ledger_is_exclusive():
    class StubRevocation(RevocationView):
        revoked = False

        async def is_revoked(self, principal):
            return self.revoked

    revocation = StubRevocation()
    governance = PlatformGovernance(
        GovernanceService(StubProfile(), revocation=revocation),
        identity={"actor": "user"},
        action_claims=MemoryActionClaimLedger(),
    )
    registry = StubRegistry()
    context = {"invocation_id": "action"}
    assert await governance.execute_tool(registry, "write", {"domain": "a"}, context) == 42
    revocation.revoked = True
    assert await governance.execute_tool(registry, "write", {"domain": "a"}, context) == 42
    assert registry.calls == 1
    with pytest.raises(GovernanceDeniedError):
        await governance.execute_tool(registry, "write", {"domain": "a"}, {"invocation_id": "new"})
    revocation.revoked = False
    context = {"invocation_id": "external", "ledger_owned": True}
    await governance.execute_tool(registry, "write", {"domain": "a"}, context)
    assert await governance._claims.resolve("external") is None


async def test_schema_and_unknown_dispatch_keep_receipt_unresolved():
    governance = PlatformGovernance(
        GovernanceService(StubProfile()), identity={"actor": "user"}, action_claims=MemoryActionClaimLedger()
    )
    registry = StubRegistry()
    with pytest.raises(GovernanceDeniedError, match="input schema"):
        await governance.execute_tool(registry, "write", {}, {"invocation_id": "bad-input"})
    assert registry.calls == 0
    registry.metadata["returns"] = {"type": "string"}
    with pytest.raises(OutcomeUnknownError, match="output schema"):
        await governance.execute_tool(registry, "write", {"domain": "a"}, {"invocation_id": "bad-output"})
    with pytest.raises(OutcomeUnknownError):
        await governance.execute_tool(registry, "write", {"domain": "a"}, {"invocation_id": "bad-output"})
    assert registry.calls == 1


async def test_approval_consumes_real_action_and_rejects_restart_epoch():
    authority = MemoryApprovalLedger()
    profile = StubProfile(ActionDecision.REQUIRE_APPROVAL)
    governance = PlatformGovernance(GovernanceService(profile, approval_ledger=authority), identity={"actor": "user"})
    arguments = {"domain": "a"}
    principal = governance.claim_principal()
    now = datetime.now(UTC)
    binding = ApprovalBinding(
        approval_ref="grant",
        principal_fingerprint=principal.fingerprint(),
        tool_name="write",
        arguments_digest=tool_arguments_digest(arguments),
        target_resources=target_resources_for(arguments),
        issued_at=now,
        expires_at=now + timedelta(hours=1),
        judgment="approved",
        one_time=True,
    )
    await authority.save(binding)
    assert (
        await governance.authorize_tool_call(
            name="write", arguments=arguments, context={"invocation_id": "a", "approval_ref": "grant"}
        )
    ).allowed
    assert not (
        await governance.authorize_tool_call(
            name="write", arguments=arguments, context={"invocation_id": "b", "approval_ref": "grant"}
        )
    ).allowed
    restarted = binding.model_copy(update={"approval_ref": "old-process", "clock_epoch": "previous-process"})
    await authority.save(restarted)
    request = ActionRequest(
        "a", "write", tool_arguments_digest(arguments), target_resources_for(arguments), principal.fingerprint()
    )
    assert (await ApprovalGate(authority).authorize("old-process", request, now=now)).check.value == "expired"


async def test_injected_facade_preserves_each_factory_identity(db_session):
    profile = StubProfile()
    facade = PlatformGovernance(GovernanceService(profile))
    registry = StubRegistry()
    first = create_runtime_port(db_session, None, registry, governance=facade, identity={"actor": "first"})
    second = create_runtime_port(db_session, None, registry, governance=facade, identity={"actor": "second"})
    await first.tool_execute("write", {"domain": "a"})
    await second.tool_execute("write", {"domain": "a"})
    assert profile.actors == ["first", "second"]
    assert facade._identity == {}


class TestApprovalBindingLifecycleEndToEnd:
    """The binding saved by the authority-backed chat callback must be
    consumable by the governed registry dispatch with the SAME envelope
    (user actor + agent principal) and parsed arguments — the s03/s11
    scripted-loop regression."""

    async def test_callback_binding_consumes_at_dispatch(self, db_session):
        import uuid
        from datetime import UTC, datetime
        from unittest.mock import AsyncMock, MagicMock

        from hecate_governance.principal import ActionPrincipal, PrincipalSource

        from hecate.enterprise.approval_binding import AuthorityBackedApprovalCallback

        agent_id = uuid.uuid4()
        user_id = uuid.uuid4()
        session_id = uuid.uuid4()
        identity = {"actor": str(user_id), "agent_principal": str(agent_id)}
        ledger = None  # replaced by the SQL authority below (db_session fixture)
        store = MagicMock()
        store.append = AsyncMock()

        from hecate.enterprise.governance_ledgers import SqlApprovalLedger

        ledger = SqlApprovalLedger(db_session)
        callback = AuthorityBackedApprovalCallback(
            authority=ledger,
            event_store=store,
            session_id=session_id,
            workspace_id=uuid.uuid4(),
            agent_id=agent_id,
            identity=identity,
        )
        callback._inner_backend = _AlwaysApproveOnce()

        arguments = {"title": "t", "body": "b"}
        decision = await callback.request_approval(
            tool_name="create_test_ticket",
            arguments={"tool_call_id": "tc-1", **arguments},
            risk_level="high",
            context={"session_id": str(session_id), "tool_call_id": "tc-1"},
        )
        assert decision.approved is True

        # The dispatch consumes through the same governance seam the
        # GovernedToolRegistry uses: build_principal over the same identity.
        from hecate_governance.action import ActionDecision
        from hecate_governance.policy import PolicyOutcome, PolicyProfile

        class _RequireApproval(PolicyProfile):
            @property
            def version(self) -> str:
                return "test-1"

            async def evaluate(self, context):
                return PolicyOutcome(ActionDecision.REQUIRE_APPROVAL, self.version)

        approval_ref = callback._references[
            callback._reference_key(
                "create_test_ticket",
                {"tool_call_id": "tc-1", **arguments},
                {"session_id": str(session_id), "tool_call_id": "tc-1"},
            )
        ]
        principal = ActionPrincipal.from_verified_claims(
            {"sub": str(user_id)},
            source=PrincipalSource.PLATFORM_ENTRY,
            domains=(),
            agent_principal=str(agent_id),
        )
        from hecate_governance.approval import ActionRequest

        request = ActionRequest(
            invocation_id="chat-dispatch-1",
            tool_name="create_test_ticket",
            arguments_digest=__import__(
                "hecate_governance.action", fromlist=["tool_arguments_digest"]
            ).tool_arguments_digest(arguments),
            target_resources=(),
            principal_fingerprint=principal.fingerprint(),
            task_scope=str(session_id),
        )
        from hecate_governance.approval import ApprovalGate

        outcome = await ApprovalGate(SqlApprovalLedger(db_session)).consume(
            approval_ref, request, now=datetime.now(UTC)
        )
        assert outcome.status.value == "consumed", outcome.status
        # A different action is refused (one-time consumption).
        other = ActionRequest(
            invocation_id="chat-dispatch-2",
            tool_name="create_test_ticket",
            arguments_digest=__import__(
                "hecate_governance.action", fromlist=["tool_arguments_digest"]
            ).tool_arguments_digest(arguments),
            target_resources=(),
            principal_fingerprint=principal.fingerprint(),
            task_scope=str(session_id),
        )
        outcome2 = await ApprovalGate(SqlApprovalLedger(db_session)).consume(approval_ref, other, now=datetime.now(UTC))
        assert outcome2.status.value == "conflict"


class _AlwaysApproveOnce:
    async def request_approval(self, *, tool_name, arguments, risk_level, context):
        from hecate.runtime.tool_access import ApprovalDecision, ApprovalScope

        return ApprovalDecision(approved=True, reason="ok", scope=ApprovalScope.ONCE)


class _RequireApprovalProfileStub(PolicyProfile):
    @property
    def version(self) -> str:
        return "stub"

    async def evaluate(self, context):
        raise NotImplementedError
