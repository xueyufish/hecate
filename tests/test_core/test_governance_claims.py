"""Ledger-less path claim/receipt idempotency (task 2.7).

Covers: concurrent duplicate invocation executes once; success with a failed
receipt write goes to reconciliation without redo; completed replay returns
the recorded result; identity conflict is refused.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from hecate_governance.claims import (
    ClaimStatus,
    MemoryActionClaimLedger,
    OutcomeUnknownError,
)

from hecate.core.composition.governance import (
    GovernanceMode,
    GovernanceService,
    PlatformGovernance,
)
from hecate.core.composition.runtime_port_adapter import _ProductionRuntimePort
from tests.test_core.test_governance_facts import _AllowProfile


class _CountingRegistry:
    def __init__(self, result: str = "ok") -> None:
        self.calls = 0
        self._result = result

    async def execute(self, name: str, args: dict, context: Any = None) -> str:
        self.calls += 1
        return self._result


class TestConcurrentDuplicate:
    async def test_concurrent_duplicates_execute_once(self) -> None:
        ledger = MemoryActionClaimLedger()
        governance = PlatformGovernance(
            GovernanceService(_AllowProfile()),
            GovernanceMode.ENFORCED,
            action_claims=ledger,
        )
        registry = _CountingRegistry()
        port = _ProductionRuntimePort(db=None, llm_service=None, tool_registry=registry, governance=governance)
        args = {"sku": "42"}

        results = await asyncio.gather(
            port.tool_execute("inventory.write", args, {"agent_id": "7", "invocation_id": "logical-call-1"}),
            port.tool_execute("inventory.write", args, {"agent_id": "7", "invocation_id": "logical-call-1"}),
            return_exceptions=True,
        )
        executed = [r for r in results if not isinstance(r, BaseException)]
        unknown = [r for r in results if isinstance(r, OutcomeUnknownError)]
        # Exactly one executed; the racing duplicate refused as in-flight
        # (outcome not established) and never reached the registry.
        assert registry.calls == 1
        assert len(executed) + len(unknown) == 2
        assert len(unknown) <= 1

    async def test_retry_after_completion_replays_recorded_result(self) -> None:
        ledger = MemoryActionClaimLedger()
        governance = PlatformGovernance(
            GovernanceService(_AllowProfile()),
            GovernanceMode.ENFORCED,
            action_claims=ledger,
        )
        registry = _CountingRegistry(result="ok-42")
        port = _ProductionRuntimePort(db=None, llm_service=None, tool_registry=registry, governance=governance)
        first = await port.tool_execute(
            "inventory.write", {"sku": "42"}, {"agent_id": "7", "invocation_id": "logical-call-1"}
        )
        assert first == "ok-42"
        # Same logical call re-submitted after the receipt completed: replay,
        # no second side effect.
        second = await port.tool_execute(
            "inventory.write", {"sku": "42"}, {"agent_id": "7", "invocation_id": "logical-call-1"}
        )
        assert second == "ok-42"
        assert registry.calls == 1

    async def test_receipt_write_failure_goes_to_reconciliation_without_redo(
        self,
    ) -> None:
        ledger = MemoryActionClaimLedger()
        ledger.fail_next_complete(RuntimeError("evidence store down"))
        governance = PlatformGovernance(
            GovernanceService(_AllowProfile()),
            GovernanceMode.ENFORCED,
            action_claims=ledger,
        )
        registry = _CountingRegistry()
        port = _ProductionRuntimePort(db=None, llm_service=None, tool_registry=registry, governance=governance)
        # The business call succeeded; the receipt could not be persisted.
        with pytest.raises(OutcomeUnknownError, match="receipt write failed"):
            await port.tool_execute(
                "inventory.write", {"sku": "42"}, {"agent_id": "7", "invocation_id": "logical-call-1"}
            )
        assert registry.calls == 1
        # The record stays unresolved: a retry does NOT redo the side effect.
        with pytest.raises(OutcomeUnknownError, match="reconciliation"):
            await port.tool_execute(
                "inventory.write", {"sku": "42"}, {"agent_id": "7", "invocation_id": "logical-call-1"}
            )
        assert registry.calls == 1

    async def test_same_id_different_arguments_conflict(self) -> None:
        # Deterministic keys differ with arguments, so force a collision on
        # the ledger directly: same id, different identity.
        ledger = MemoryActionClaimLedger()
        first = await ledger.claim(
            "tool-x",
            tool_name="a",
            arguments_digest="d1",
            principal_fingerprint="p1",
            side_effect_class="non_idempotent_write",
        )
        assert first.status is ClaimStatus.CLAIMED
        conflict = await ledger.claim(
            "tool-x",
            tool_name="a",
            arguments_digest="d2",
            principal_fingerprint="p1",
            side_effect_class="non_idempotent_write",
        )
        assert conflict.status is ClaimStatus.CONFLICT

    async def test_outcome_unknown_flag_classified_by_runtime(self) -> None:
        # The runtime receipt classifier treats governance claim refusals as
        # unknown outcomes (reconciliation), not clean failures.
        from hecate_runtime.workers.tool_worker import _is_indeterminate_error

        assert _is_indeterminate_error(OutcomeUnknownError("dup")) is True
        assert _is_indeterminate_error(ValueError("plain")) is False

    async def test_dispatch_exception_keeps_claim_unresolved(self) -> None:
        ledger = MemoryActionClaimLedger()
        governance = PlatformGovernance(
            GovernanceService(_AllowProfile()),
            GovernanceMode.ENFORCED,
            action_claims=ledger,
        )
        registry = _CountingRegistry()

        class _FailingOnce:
            def __init__(self, inner: _CountingRegistry) -> None:
                self._inner = inner
                self.failed = False

            async def execute(self, name: str, args: dict, context: Any = None) -> str:
                if not self.failed:
                    self.failed = True
                    raise RuntimeError("business rejected")
                return await self._inner.execute(name, args, context)

        failing = _FailingOnce(registry)
        port = _ProductionRuntimePort(db=None, llm_service=None, tool_registry=failing, governance=governance)
        args = {"sku": "9"}
        with pytest.raises(OutcomeUnknownError, match="business rejected"):
            await port.tool_execute("inventory.write", args, {"agent_id": "7", "invocation_id": "logical-call-1"})
        # A generic exception cannot prove that the executor performed no effect.
        with pytest.raises(OutcomeUnknownError):
            await port.tool_execute("inventory.write", args, {"agent_id": "7", "invocation_id": "logical-call-1"})
        assert registry.calls == 0
