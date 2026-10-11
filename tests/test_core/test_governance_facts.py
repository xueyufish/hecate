"""Tool facts, compensation gating and delegation intersection (task 2.6)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from hecate_governance.delegation import DelegationLink, effective_delegation_domains
from hecate_governance.facts import CompensationDeclaration, CompensationRegistry
from hecate_governance.policy import GovernanceService, PolicyProfile

from hecate.core.composition.governance import (
    GovernanceMode,
    PlatformGovernance,
    build_platform_governance,
)


class _AllowProfile(PolicyProfile):
    @property
    def version(self) -> str:
        return "allow-1"

    async def evaluate(self, context) -> object:
        from hecate_governance.action import ActionDecision
        from hecate_governance.policy import PolicyOutcome

        return PolicyOutcome(ActionDecision.ALLOW, self.version)


def _governance(**kwargs: Any) -> tuple[PlatformGovernance, list[dict]]:
    facts: list[dict] = []
    service = GovernanceService(_AllowProfile())
    governance = PlatformGovernance(
        service, GovernanceMode.ENFORCED, facts_sink=facts.append, identity={"actor": "agent:7"}, **kwargs
    )
    return governance, facts


class TestToolFacts:
    async def test_candidate_and_actual_facts_correlated(self) -> None:
        governance, facts = _governance()
        governance.record_tool_facts(
            invocation_id="decision-1",
            candidates=[
                {"tool_name": "query_inventory", "arguments_digest": "d1"},
                {"tool_name": "write_file", "arguments_digest": "d2"},
            ],
            context={"session_id": "s-1", "agent_principal": "agent-7"},
        )
        governance.record_tool_facts(
            invocation_id="actual:query_inventory:d1",
            candidates=[],
            actual={"tool_name": "query_inventory", "arguments_digest": "d1"},
            context={"session_id": "s-1"},
        )
        assert len(facts) == 2
        assert [c["tool_name"] for c in facts[0]["candidates"]] == [
            "query_inventory",
            "write_file",
        ]
        assert facts[0]["session_ref"] == "s-1"
        assert facts[1]["actual"]["tool_name"] == "query_inventory"

    async def test_repeated_actual_updates_same_record(self) -> None:
        governance, facts = _governance()
        governance.record_tool_facts(
            invocation_id="actual:t:d",
            candidates=[],
            actual={"tool_name": "t", "arguments_digest": "d"},
        )
        governance.record_tool_facts(
            invocation_id="actual:t:d",
            candidates=[{"tool_name": "other", "arguments_digest": "x"}],
        )
        assert len(facts) == 2
        # Same invocation id merges: the record keeps both halves.
        assert facts[1]["actual"]["tool_name"] == "t"
        assert facts[1]["candidates"][0]["tool_name"] == "other"

    async def test_port_records_candidates_and_actual(self, monkeypatch) -> None:
        import hashlib
        import json as _json
        from types import SimpleNamespace

        from hecate.core.composition.runtime_port_adapter import _ProductionRuntimePort

        delta = SimpleNamespace(
            id="tc1",
            type="function",
            function=SimpleNamespace(
                name="query_inventory",
                arguments=_json.dumps({"domain": "domain_a", "sku": "S1"}),
            ),
        )

        class _StreamLLM:
            async def chat_stream(self, **kwargs: Any):
                yield {"content": "thinking"}
                yield {"content": None, "tool_calls": [delta]}

        class _Registry:
            async def execute(self, name: str, args: dict, context: Any = None) -> str:
                return "ok"

        governance, facts = _governance()
        port = _ProductionRuntimePort(
            db=None,
            llm_service=_StreamLLM(),
            tool_registry=_Registry(),
            governance=governance,
        )
        chunks = []
        async for chunk in port.llm_invoke_structured([], {"model": "m"}):
            chunks.append(chunk)
        assert any(c.get("tool_calls") for c in chunks)
        candidate_records = [f for f in facts if f["candidates"]]
        assert len(candidate_records) == 1
        expected_digest = hashlib.sha256(
            _json.dumps({"domain": "domain_a", "sku": "S1"}, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        assert candidate_records[0]["candidates"][0]["arguments_digest"] == expected_digest

        await port.tool_execute("query_inventory", {"domain": "domain_a", "sku": "S1"}, {"agent_id": "7"})
        actual_records = [f for f in facts if f["actual"]]
        assert len(actual_records) == 1
        assert actual_records[0]["actual"]["tool_name"] == "query_inventory"


class TestCompensationGating:
    async def test_undeclared_compensation_rejected(self) -> None:
        governance, _ = _governance(compensations=CompensationRegistry())
        decision = await governance.authorize_tool_call(
            name="cancel_order",
            arguments={"order_id": "o-1"},
            context={
                "compensates_action_id": "tool-orig",
                "compensates_tool": "place_order",
                "session_id": "s-1",
            },
        )
        assert not decision.allowed
        assert "not a declared, approved compensation" in decision.reason

    async def test_declared_and_approved_compensation_allowed(self) -> None:
        registry = CompensationRegistry()
        registry.declare(
            CompensationDeclaration(
                tool_name="cancel_order",
                compensates_tool_name="place_order",
                applies_when="order not yet shipped",
                approved=True,
            )
        )
        governance, facts = _governance(compensations=registry)
        decision = await governance.authorize_tool_call(
            name="cancel_order",
            arguments={"order_id": "o-1"},
            context={
                "compensates_action_id": "tool-orig",
                "compensates_tool": "place_order",
                "session_id": "s-1",
            },
        )
        assert decision.allowed

    async def test_declared_but_unapproved_rejected(self) -> None:
        registry = CompensationRegistry()
        registry.declare(
            CompensationDeclaration(
                tool_name="cancel_order",
                compensates_tool_name="place_order",
                applies_when="order not yet shipped",
                approved=False,
            )
        )
        governance, _ = _governance(compensations=registry)
        decision = await governance.authorize_tool_call(
            name="cancel_order",
            arguments={"order_id": "o-1"},
            context={"compensates_action_id": "x", "compensates_tool": "place_order"},
        )
        assert not decision.allowed

    async def test_no_compensation_reference_is_unaffected(self) -> None:
        governance, _ = _governance(compensations=CompensationRegistry())
        decision = await governance.authorize_tool_call(name="place_order", arguments={"order_id": "o-1"}, context={})
        assert decision.allowed


class TestDelegationIntersection:
    async def test_child_request_outside_parent_grant_denied_with_chain(self) -> None:
        governance, _ = _governance()
        decision = await governance.authorize_tool_call(
            name="finance.export",
            arguments={"report": "q3"},
            context={"session_id": "s-1"},
            identity={
                "actor": "agent:child-1",
                "delegation_id": "dele-9",
                "delegation_grantor": "agent:parent-1",
                "delegation_domains": ("inventory",),
                "data_domains": ("finance",),
            },
        )
        assert not decision.allowed
        assert "dele-9" in decision.reason
        assert "finance" in decision.reason

    async def test_child_request_within_grant_allowed(self) -> None:
        governance, _ = _governance()
        decision = await governance.authorize_tool_call(
            name="inventory.write",
            arguments={"sku": "42"},
            context={"session_id": "s-1"},
            identity={
                "actor": "agent:child-1",
                "delegation_id": "dele-9",
                "delegation_grantor": "agent:parent-1",
                "delegation_domains": ("inventory",),
                "data_domains": ("inventory",),
            },
        )
        assert decision.allowed

    def test_intersection_across_chain_hops(self) -> None:
        now = datetime.now(UTC)
        chain = (
            DelegationLink("d1", "org", "parent", ("inventory", "finance")),
            DelegationLink("d2", "parent", "child", ("inventory",)),
        )
        within = effective_delegation_domains(chain, ("inventory",), now=now)
        assert within.allowed
        assert within.effective_domains == ("inventory",)
        # Requesting beyond the chain intersection (d1 grants finance, d2
        # does not) is denied against the effective grant.
        beyond = effective_delegation_domains(chain, ("inventory", "finance"), now=now)
        assert not beyond.allowed
        assert "finance" in beyond.reason

    def test_empty_chain_denies_by_default(self) -> None:
        decision = effective_delegation_domains((), ("inventory",), now=datetime.now(UTC))
        assert not decision.allowed
        assert "default" in decision.reason

    def test_expired_hop_denies(self) -> None:
        past = datetime(2020, 1, 1, tzinfo=UTC)
        chain = (DelegationLink("d1", "org", "child", ("inventory",), expires_at=past),)
        decision = effective_delegation_domains(chain, ("inventory",), now=datetime.now(UTC))
        assert not decision.allowed
        assert "expired" in decision.reason


class TestFactoryWiringDefaults:
    async def test_default_governance_records_facts_without_sink(self, caplog) -> None:
        governance = build_platform_governance(None, mode=GovernanceMode.ENFORCED)
        governance.record_tool_facts(
            invocation_id="decision-2",
            candidates=[{"tool_name": "t", "arguments_digest": "d"}],
        )
        # No sink: structured log carries the facts (no exception).
        assert governance._facts["decision-2"].candidates[0].tool_name == "t"
