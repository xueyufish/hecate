"""SQL authority ledgers: approval bindings and action claims (task 3.1)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from hecate_governance.approval import ActionRequest, ApprovalBinding, ApprovalJudgment
from hecate_governance.claims import ClaimStatus
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.enterprise.governance_ledgers import (
    SqlActionClaimLedger,
    SqlApprovalLedger,
    project_decision_to_record,
)
from hecate.models.approval import ApprovalAuthorityModel

NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=UTC)


def _principal_fingerprint(actor: str = "user-1") -> str:
    from hecate_governance.principal import ActionPrincipal, PrincipalSource

    return ActionPrincipal.from_verified_claims(
        {"sub": actor}, source=PrincipalSource.PLATFORM_ENTRY, domains=("inventory",)
    ).fingerprint()


def _request(invocation_id: str = "tool-abc") -> ActionRequest:
    return ActionRequest(
        invocation_id=invocation_id,
        tool_name="inventory.write",
        arguments_digest="digest-1",
        target_resources=("sku-42",),
        principal_fingerprint=_principal_fingerprint(),
        deployment_id="dealer-app",
        domains=("inventory",),
        task_scope="task-1",
    )


async def _binding(ledger: SqlApprovalLedger, ref: str = "appr-1", **overrides) -> ApprovalBinding:
    base: dict = {
        "approval_ref": ref,
        "principal_fingerprint": _principal_fingerprint(),
        "tool_name": "inventory.write",
        "arguments_digest": "digest-1",
        "target_resources": ("sku-42",),
        "deployment_id": "dealer-app",
        "domains": ("inventory",),
        "task_scope": "task-1",
        "issued_at": NOW - timedelta(minutes=5),
    }
    base.update(overrides)
    binding = ApprovalBinding(**base)
    await ledger.save(binding)
    if binding.judgment is ApprovalJudgment.PENDING:
        await ledger.decide(ref, approved=True, reason="ok", decided_by="approver-1")
    return await ledger.get(ref)  # type: ignore[return-value]


class TestSqlApprovalLedger:
    async def test_save_decide_consume_round_trip(self, db_session: AsyncSession) -> None:
        ledger = SqlApprovalLedger(db_session)
        await _binding(ledger)
        outcome = await ledger.consume("appr-1", _request(), now=NOW)
        assert outcome.status.value == "consumed"
        stored = await ledger.get("appr-1")
        assert stored is not None
        assert stored.consumed_by_invocation_id == "tool-abc"

    async def test_duplicate_ref_rejected(self, db_session: AsyncSession) -> None:
        ledger = SqlApprovalLedger(db_session)
        await _binding(ledger, "appr-dup")
        with pytest.raises(ValueError, match="already exists"):
            await _binding(ledger, "appr-dup")

    async def test_consume_race_only_one_wins(self, db_session: AsyncSession) -> None:
        ledger = SqlApprovalLedger(db_session)
        await _binding(ledger, "appr-race")
        outcomes = await asyncio.gather(
            ledger.consume("appr-race", _request("tool-a"), now=NOW),
            ledger.consume("appr-race", _request("tool-b"), now=NOW),
        )
        statuses = sorted(o.status.value for o in outcomes)
        assert statuses.count("consumed") == 1
        assert "conflict" in statuses

    async def test_same_action_rebinds_after_crash(self, db_session: AsyncSession) -> None:
        ledger = SqlApprovalLedger(db_session)
        await _binding(ledger, "appr-rebind")
        await ledger.consume("appr-rebind", _request("tool-x"), now=NOW)
        again = await ledger.consume("appr-rebind", _request("tool-x"), now=NOW)
        assert again.status.value == "same_action_rebound"

    async def test_revocation_covers_approval(self, db_session: AsyncSession) -> None:
        ledger = SqlApprovalLedger(db_session)
        await _binding(ledger, "appr-revoke")
        await ledger.revoke("appr-revoke")
        outcome = await ledger.consume("appr-revoke", _request(), now=NOW)
        assert outcome.status.value == "revoked"

    async def test_expired_approval_not_consumed(self, db_session: AsyncSession) -> None:
        ledger = SqlApprovalLedger(db_session)
        await _binding(ledger, "appr-exp", expires_at=NOW - timedelta(seconds=1))
        outcome = await ledger.consume("appr-exp", _request(), now=NOW)
        assert outcome.status.value == "expired"


class TestProjectionRebuild:
    async def test_rebuild_does_not_resurrect_consumption(self, db_session: AsyncSession) -> None:
        from hecate.runtime.security.approval import rebuild_approval_projection

        ledger = SqlApprovalLedger(db_session)
        await _binding(ledger, "appr-proj")
        await project_decision_to_record(
            db_session,
            workspace_id=uuid.UUID(int=1),
            session_id=uuid.UUID(int=2),
            binding=(await ledger.get("appr-proj")),  # type: ignore[arg-type]
        )
        await ledger.consume("appr-proj", _request("tool-proj"), now=NOW)
        # The projection rebuild derives from events; whatever it writes, the
        # authority still arbitrates: the consumed approval blocks others.
        from unittest.mock import MagicMock

        store = MagicMock()
        store.get_events = _empty_events
        written = await rebuild_approval_projection(
            store, uuid.UUID(int=2), projection_writer=None, workspace_id=uuid.UUID(int=1)
        )
        assert written == 0
        second = await ledger.consume("appr-proj", _request("tool-other"), now=NOW)
        assert second.status.value == "conflict"

    async def test_projection_row_carries_binding_shape(self, db_session: AsyncSession) -> None:
        from sqlalchemy import select

        from hecate.models.approval import ApprovalRecordModel

        ledger = SqlApprovalLedger(db_session)
        binding = await _binding(ledger, "appr-shape")
        await project_decision_to_record(
            db_session,
            workspace_id=uuid.UUID(int=1),
            session_id=uuid.UUID(int=2),
            binding=binding,  # type: ignore[arg-type]
        )
        row = (
            await db_session.execute(
                select(ApprovalRecordModel).where(ApprovalRecordModel.approval_ref == "appr-shape")
            )
        ).scalar_one()
        assert row.arguments_digest == "digest-1"
        assert row.target_resources == ["sku-42"]
        assert row.decision_source == "governance"
        assert row.status == "approved"


async def _empty_events(session_id):  # noqa: ANN001
    return []


class TestSqlActionClaims:
    async def test_claim_then_replay(self, db_session: AsyncSession) -> None:
        ledger = SqlActionClaimLedger(db_session)
        first = await ledger.claim(
            "tool:inv:abc",
            tool_name="inventory.write",
            arguments_digest="d1",
            principal_fingerprint=_principal_fingerprint(),
            side_effect_class="non_idempotent_write",
        )
        assert first.status is ClaimStatus.CLAIMED
        await ledger.complete("tool:inv:abc", succeeded=True, result={"qty": 1}, result_digest="rd")
        replay = await ledger.claim(
            "tool:inv:abc",
            tool_name="inventory.write",
            arguments_digest="d1",
            principal_fingerprint=_principal_fingerprint(),
            side_effect_class="non_idempotent_write",
        )
        assert replay.status is ClaimStatus.COMPLETED_REPLAY
        assert replay.record.result == {"qty": 1}

    async def test_open_claim_blocks_second_claim(self, db_session: AsyncSession) -> None:
        # True connection-level races are enforced by the partial unique index
        # (uq_action_claims_open) and covered by the PG process matrix; this
        # test pins the ledger decision semantics over the same rows.
        ledger = SqlActionClaimLedger(db_session)
        first = await ledger.claim(
            "tool:race:1",
            tool_name="inventory.write",
            arguments_digest="d1",
            principal_fingerprint=_principal_fingerprint(),
            side_effect_class="non_idempotent_write",
        )
        assert first.status is ClaimStatus.CLAIMED
        second = await ledger.claim(
            "tool:race:1",
            tool_name="inventory.write",
            arguments_digest="d1",
            principal_fingerprint=_principal_fingerprint(),
            side_effect_class="non_idempotent_write",
        )
        assert second.status is ClaimStatus.DUPLICATE_IN_FLIGHT

    async def test_identity_conflict(self, db_session: AsyncSession) -> None:
        ledger = SqlActionClaimLedger(db_session)
        await ledger.claim(
            "tool:c:1",
            tool_name="a",
            arguments_digest="d1",
            principal_fingerprint="p1",
            side_effect_class="write",
        )
        conflict = await ledger.claim(
            "tool:c:1",
            tool_name="a",
            arguments_digest="d2",
            principal_fingerprint="p1",
            side_effect_class="write",
        )
        assert conflict.status is ClaimStatus.CONFLICT

    async def test_terminal_failure_allows_new_attempt(self, db_session: AsyncSession) -> None:
        ledger = SqlActionClaimLedger(db_session)
        await ledger.claim(
            "tool:f:1",
            tool_name="a",
            arguments_digest="d1",
            principal_fingerprint="p1",
            side_effect_class="write",
        )
        await ledger.complete("tool:f:1", succeeded=False)
        again = await ledger.claim(
            "tool:f:1",
            tool_name="a",
            arguments_digest="d1",
            principal_fingerprint="p1",
            side_effect_class="write",
        )
        assert again.status is ClaimStatus.CLAIMED


class TestMigrationArtifact:
    def test_authority_table_in_metadata(self) -> None:
        from hecate.models.base import BaseModel as HecateBase

        tables = HecateBase.metadata.tables
        assert "approval_authority" in tables
        assert "action_claims" in tables
        cols = {c.name for c in tables["approval_records"].columns}
        assert {"approval_ref", "arguments_digest", "target_resources", "decision_source"} <= cols
        # The authority row carries the consumption placeholder.
        authority_cols = {c.name for c in tables["approval_authority"].columns}
        assert "consumed_by_invocation_id" in authority_cols
        # Silence unused import guard for direct ORM use in other tests.
        _ = ApprovalAuthorityModel
