"""SQL-backed approval authority and action-claim ledgers (step7 local slice).

Platform adapters implementing the governance package seams:

- ``SqlApprovalLedger`` — the authoritative approval binding store. One row
  per ``approval_ref``; one-time consumption is a conditional update on
  ``consumed_by_invocation_id`` (the CAS), so two racing actions yield at
  most one consumption and the loser gets an explicit conflict.
- ``SqlActionClaimLedger`` — claim/receipt authority for ledger-less paths.
  At-most-once dispatch rides a partial unique index over open claims; a
  lost race re-reads as duplicate-in-flight.

Consumption and revocation are written here FIRST; the ``approval_records``
projection is derived state (rebuilt from the event log) and never
arbitrates — a projection rebuild cannot resurrect a consumed approval.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from hecate_governance.approval import (
    ActionRequest,
    ApprovalBinding,
    ApprovalJudgment,
    ApprovalLedger,
    ConsumeOutcome,
    ConsumeStatus,
    check_binding,
)
from hecate_governance.claims import (
    ActionClaimLedger,
    ClaimOutcome,
    ClaimRecord,
    ClaimStatus,
    identity_matches,
)
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hecate.models.approval import ActionClaimModel, ApprovalAuthorityModel

logger = logging.getLogger(__name__)


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes even for timezone=True columns."""
    if dt is None or dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=UTC)


def _binding_from_row(row: ApprovalAuthorityModel) -> ApprovalBinding:
    return ApprovalBinding(
        approval_ref=row.approval_ref,
        principal_fingerprint=row.principal_fingerprint,
        tool_name=row.tool_name,
        arguments_digest=row.arguments_digest,
        target_resources=tuple(row.target_resources or ()),
        deployment_id=row.deployment_id,
        domains=tuple(row.domains or ()),
        task_scope=row.task_scope,
        issued_at=_aware(row.issued_at) or row.issued_at,
        expires_at=_aware(row.expires_at),
        one_time=row.one_time,
        judgment=ApprovalJudgment(row.judgment),
        reason=row.reason or "",
        decided_by=row.decided_by,
        revoked=row.revoked,
        consumed_by_invocation_id=row.consumed_by_invocation_id,
        anchor_wall=_aware(row.anchor_wall) or row.issued_at,
        anchor_monotonic=row.anchor_monotonic or 0.0,
        clock_epoch=row.clock_epoch or "legacy-untrusted",
    )


class SqlApprovalLedger(ApprovalLedger):
    """Authoritative approval bindings over ``approval_authority``."""

    def __init__(self, db: AsyncSession, *, session_factory: async_sessionmaker | None = None) -> None:
        self._db = db
        self._factory = session_factory

    async def save(self, binding: ApprovalBinding) -> None:
        if self._factory is not None:
            async with self._factory.begin() as db:
                return await SqlApprovalLedger(db).save(binding)
        exists = await self._db.execute(
            select(ApprovalAuthorityModel.id).where(
                ApprovalAuthorityModel.approval_ref == binding.approval_ref,
                ApprovalAuthorityModel.deleted.is_(False),
            )
        )
        if exists.first() is not None:
            raise ValueError(f"approval_ref {binding.approval_ref!r} already exists")
        self._db.add(
            ApprovalAuthorityModel(
                approval_ref=binding.approval_ref,
                principal_fingerprint=binding.principal_fingerprint,
                tool_name=binding.tool_name,
                arguments_digest=binding.arguments_digest,
                target_resources=list(binding.target_resources),
                deployment_id=binding.deployment_id,
                domains=list(binding.domains),
                task_scope=binding.task_scope,
                anchor_wall=binding.anchor_wall,
                anchor_monotonic=binding.anchor_monotonic,
                clock_epoch=binding.clock_epoch,
                issued_at=binding.issued_at,
                expires_at=binding.expires_at,
                one_time=binding.one_time,
                judgment=binding.judgment.value,
                reason=binding.reason,
                decided_by=binding.decided_by,
                revoked=binding.revoked,
            )
        )
        await self._db.flush()

    async def get(self, approval_ref: str) -> ApprovalBinding | None:
        if self._factory is not None:
            async with self._factory.begin() as db:
                return await SqlApprovalLedger(db).get(approval_ref)
        row = await self._get_row(approval_ref)
        return _binding_from_row(row) if row is not None else None

    async def decide(
        self, approval_ref: str, *, approved: bool, reason: str, decided_by: str | None
    ) -> ApprovalBinding:
        if self._factory is not None:
            async with self._factory.begin() as db:
                return await SqlApprovalLedger(db).decide(
                    approval_ref, approved=approved, reason=reason, decided_by=decided_by
                )
        row = await self._get_row(approval_ref)
        if row is None:
            raise KeyError(f"unknown approval_ref {approval_ref!r}")
        if row.judgment != ApprovalJudgment.PENDING.value:
            raise ValueError(f"approval {approval_ref!r} already decided")
        row.judgment = ApprovalJudgment.APPROVED.value if approved else ApprovalJudgment.REJECTED.value
        row.reason = reason
        row.decided_by = decided_by
        await self._db.flush()
        return _binding_from_row(row)

    async def revoke(self, approval_ref: str) -> ApprovalBinding:
        if self._factory is not None:
            async with self._factory.begin() as db:
                return await SqlApprovalLedger(db).revoke(approval_ref)
        row = await self._get_row(approval_ref)
        if row is None:
            raise KeyError(f"unknown approval_ref {approval_ref!r}")
        row.revoked = True
        await self._db.flush()
        return _binding_from_row(row)

    async def consume(self, approval_ref: str, request: ActionRequest, *, now: datetime) -> ConsumeOutcome:
        if self._factory is not None:
            async with self._factory.begin() as db:
                return await SqlApprovalLedger(db).consume(approval_ref, request, now=now)
        row = await self._get_row(approval_ref)
        if row is None:
            return ConsumeOutcome(ConsumeStatus.NOT_APPROVED, None)
        binding = _binding_from_row(row)
        verdict = check_binding(binding, request, now=now)
        if verdict.check.value == "revoked":
            return ConsumeOutcome(ConsumeStatus.REVOKED, binding)
        if verdict.check.value in ("not_approved", "expired"):
            status = ConsumeStatus.NOT_APPROVED if verdict.check.value == "not_approved" else ConsumeStatus.EXPIRED
            return ConsumeOutcome(status, binding)
        if verdict.check.value == "mismatch":
            return ConsumeOutcome(ConsumeStatus.CONFLICT, binding)
        if not binding.one_time:
            return ConsumeOutcome(ConsumeStatus.CONSUMED, binding)
        if row.consumed_by_invocation_id is not None:
            if row.consumed_by_invocation_id == request.invocation_id:
                return ConsumeOutcome(ConsumeStatus.SAME_ACTION_REBOUND, binding)
            return ConsumeOutcome(ConsumeStatus.CONFLICT, binding)
        # The CAS: only an unclaimed, approved row can be claimed. A racing
        # consumer's UPDATE matches zero rows — it re-reads and loses.
        # Expiry is enforced by check_binding above; the CAS stays free of
        # datetime comparisons — SQLite's ORM evaluator compares the pending
        # object's tz-aware attribute against the bound parameter and raises
        # on mixed offsets (the s03/s11 scripted-loop failure).
        result = await self._db.execute(
            update(ApprovalAuthorityModel)
            .where(
                ApprovalAuthorityModel.approval_ref == approval_ref,
                ApprovalAuthorityModel.consumed_by_invocation_id.is_(None),
                ApprovalAuthorityModel.judgment == ApprovalJudgment.APPROVED.value,
                ApprovalAuthorityModel.revoked.is_(False),
            )
            .values(consumed_by_invocation_id=request.invocation_id)
        )
        await self._db.flush()
        if result.rowcount == 0:
            refreshed = await self._get_row(approval_ref)
            return ConsumeOutcome(
                ConsumeStatus.CONFLICT,
                _binding_from_row(refreshed) if refreshed else None,
            )
        claimed = await self._get_row(approval_ref)
        return ConsumeOutcome(ConsumeStatus.CONSUMED, _binding_from_row(claimed) if claimed else None)

    async def _get_row(self, approval_ref: str) -> ApprovalAuthorityModel | None:
        result = await self._db.execute(
            select(ApprovalAuthorityModel).where(
                ApprovalAuthorityModel.approval_ref == approval_ref,
                ApprovalAuthorityModel.deleted.is_(False),
            )
        )
        return result.scalar_one_or_none()


class SqlActionClaimLedger(ActionClaimLedger):
    """Claim/receipt authority over ``action_claims`` (permanent unique invocation identity)."""

    def __init__(self, db: AsyncSession, *, session_factory: async_sessionmaker | None = None) -> None:
        self._db = db
        self._factory = session_factory

    async def _latest(self, invocation_key: str) -> ClaimRecord | None:
        result = await self._db.execute(
            select(ActionClaimModel)
            .where(
                ActionClaimModel.invocation_key == invocation_key,
                ActionClaimModel.deleted.is_(False),
            )
            .order_by(ActionClaimModel.created_at.desc(), ActionClaimModel.id.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return ClaimRecord(
            invocation_id=row.invocation_key,
            tool_name=row.tool_name,
            arguments_digest=row.arguments_digest,
            principal_fingerprint=row.principal_fingerprint,
            side_effect_class=row.side_effect_class,
            completed=row.completed,
            succeeded=row.succeeded,
            result=row.result,
            result_digest=row.result_digest,
        )

    async def claim(
        self,
        invocation_id: str,
        *,
        tool_name: str,
        arguments_digest: str,
        principal_fingerprint: str,
        side_effect_class: str,
    ) -> ClaimOutcome:
        if self._factory is not None:
            async with self._factory.begin() as db:
                return await SqlActionClaimLedger(db).claim(
                    invocation_id,
                    tool_name=tool_name,
                    arguments_digest=arguments_digest,
                    principal_fingerprint=principal_fingerprint,
                    side_effect_class=side_effect_class,
                )
        latest = await self._latest(invocation_id)
        if latest is not None:
            if not identity_matches(latest, tool_name, arguments_digest, principal_fingerprint):
                return ClaimOutcome(ClaimStatus.CONFLICT, latest, "invocation identity differs")
            if latest.completed and latest.succeeded:
                return ClaimOutcome(ClaimStatus.COMPLETED_REPLAY, latest)
            if not latest.completed:
                return ClaimOutcome(ClaimStatus.DUPLICATE_IN_FLIGHT, latest, "reconciliation required")
            result = await self._db.execute(
                update(ActionClaimModel)
                .where(
                    ActionClaimModel.invocation_key == invocation_id,
                    ActionClaimModel.completed.is_(True),
                    ActionClaimModel.succeeded.is_(False),
                )
                .values(completed=False, succeeded=None, result=None, result_digest=None)
            )
            await self._db.flush()
            if result.rowcount != 1:
                return ClaimOutcome(ClaimStatus.DUPLICATE_IN_FLIGHT, latest, "claim race lost")
            return ClaimOutcome(ClaimStatus.CLAIMED, await self._latest(invocation_id))
        row = ActionClaimModel(
            invocation_key=invocation_id,
            tool_name=tool_name,
            arguments_digest=arguments_digest,
            principal_fingerprint=principal_fingerprint,
            side_effect_class=side_effect_class,
        )
        try:
            async with self._db.begin_nested():
                self._db.add(row)
                await self._db.flush()
        except IntegrityError:
            latest = await self._latest(invocation_id)
            if latest is None:
                raise
            if not identity_matches(latest, tool_name, arguments_digest, principal_fingerprint):
                return ClaimOutcome(ClaimStatus.CONFLICT, latest, "invocation identity differs")
            status = (
                ClaimStatus.COMPLETED_REPLAY
                if latest.completed and latest.succeeded
                else ClaimStatus.DUPLICATE_IN_FLIGHT
            )
            return ClaimOutcome(status, latest, "concurrent invocation already claimed")
        record = ClaimRecord(
            invocation_id=row.invocation_key,
            tool_name=row.tool_name,
            arguments_digest=row.arguments_digest,
            principal_fingerprint=row.principal_fingerprint,
            side_effect_class=row.side_effect_class,
        )
        return ClaimOutcome(ClaimStatus.CLAIMED, record)

    async def complete(
        self,
        invocation_id: str,
        *,
        succeeded: bool,
        result: Any = None,
        result_digest: str | None = None,
    ) -> None:
        if self._factory is not None:
            async with self._factory.begin() as db:
                return await SqlActionClaimLedger(db).complete(
                    invocation_id, succeeded=succeeded, result=result, result_digest=result_digest
                )
        row = (
            await self._db.execute(
                select(ActionClaimModel)
                .where(
                    ActionClaimModel.invocation_key == invocation_id,
                    ActionClaimModel.deleted.is_(False),
                )
                .order_by(ActionClaimModel.created_at.desc(), ActionClaimModel.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            raise KeyError(f"unknown invocation {invocation_id!r}")
        row.completed = True
        row.succeeded = succeeded
        row.result = result
        row.result_digest = result_digest
        await self._db.flush()

    async def resolve(self, invocation_id: str) -> ClaimRecord | None:
        if self._factory is not None:
            async with self._factory() as db:
                return await SqlActionClaimLedger(db).resolve(invocation_id)
        return await self._latest(invocation_id)


async def project_decision_to_record(
    db: AsyncSession,
    *,
    workspace_id: Any,
    session_id: Any,
    binding: ApprovalBinding,
    risk_level: str = "high",
    decision_source: str = "governance",
) -> None:
    """Write the projection row mirroring one authority decision.

    The projection (``approval_records``) is derived state; this helper gives
    the event-log rebuild the same shape decisions carry (digest, resources,
    approval reference). Consumption state is deliberately NOT projected —
    it lives only in the authority.
    """
    from hecate.models.approval import ApprovalRecordModel

    db.add(
        ApprovalRecordModel(
            workspace_id=workspace_id,
            tool_name=binding.tool_name,
            session_id=session_id,
            scope="once" if binding.one_time else "session",
            status=binding.judgment.value,
            risk_level=risk_level,
            reason=binding.reason,
            expires_at=binding.expires_at,
            approval_ref=binding.approval_ref,
            arguments_digest=binding.arguments_digest,
            target_resources=list(binding.target_resources),
            decision_source=decision_source,
        )
    )
    await db.flush()
