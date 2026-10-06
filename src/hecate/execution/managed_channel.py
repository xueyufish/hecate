"""Platform-side managed channel (step6/7 managed runner enrollment).

Three faces, one module:

- **authenticate_host** — the channel credential gate: signature/expiry/
  audience via :mod:`hecate.execution.managed_credentials`, then the claims
  must name a live workspace and an ADMITTED enrollment with
  ``managed_new_runs`` whose host identity matches the credential subject.
  Client-asserted fields are ignored by design — authorization comes only
  from the verified credential plus the enrollment row.
- **ManagedDeliveryService** — the single writer of ``managed_deliveries``.
  A delivery row is the persisted dispatch intent (the outbox property:
  intent exists before the host acts); the host pulls batches by row-id
  cursor and acknowledges with its local task/run references. Redelivery
  after a lost response is how reconciliation works — the host's idempotent
  accept makes it harmless, and ``accept`` rejects a conflicting mapping.
- **ManagedProjectionService** — the receive side of the event mirror: the
  host uploads durable-log envelopes; projection deduplicates on event_id
  (the read model's unique index), maps the host's local task references to
  platform runs through the delivery's accepted mapping, and updates only
  the platform Run projection. Nothing here ever writes back to the host.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.execution.managed_credentials import (
    Claims,
    CredentialError,
    issue_credential,
    lease_claims,
    verify_credential,
)
from hecate.models.managed_delivery import ManagedDeliveryModel
from hecate.models.standalone_enrollment import AdmissionResult, StandaloneEnrollmentModel

DELIVERY_PENDING = "pending"
DELIVERY_DELIVERED = "delivered"

# Event families the host may project (the mirror's allowlist; everything
# else in the host's log stays authoritative there).
PROJECTABLE_EVENT_TYPES = frozenset(
    {"task_submitted", "task_state", "command_recorded", "command_state", "run_terminal"}
)


class ManagedChannelError(Exception):
    """Managed channel failure (4xx semantics on the channel API)."""


class CredentialAuthenticationError(ManagedChannelError):
    """Missing/invalid/expired channel credential (401 semantics)."""


class EnrollmentNotActiveError(ManagedChannelError):
    """The enrollment is not admitted for managed new runs (403 semantics)."""


class DeliveryConflictError(ManagedChannelError):
    """An accept maps a delivery to different local references (409 semantics)."""


@dataclass(frozen=True)
class HostContext:
    """The verified host identity behind one channel request.

    ``managed_scope`` is the enrollment's operator-controlled data-domain
    allowlist; it travels with the host so pull responses can carry it
    inside the issued lease (step6b).
    """

    workspace_id: uuid.UUID
    enrollment_id: uuid.UUID
    deployment_domain: str
    host_name: str
    claims: Claims
    managed_scope: list[str]


async def authenticate_host(
    db: AsyncSession,
    token: dict[str, Any],
    *,
    secrets_by_issuer: dict[str, bytes],
    now: datetime | None = None,
) -> HostContext:
    """Verify a channel credential and bind it to an admitted enrollment.

    ``secrets_by_issuer`` maps issuer domain -> provisioned signing secret
    (operator material; the platform stores digests only). After signature/
    expiry/audience verification the claims' ``tenant`` must name a live
    workspace and the (iss, sub) pair must match one ADMITTED enrollment
    with ``managed_new_runs`` set — a revoked or de-opted enrollment fails
    here on every request, reconnect included.
    """

    iss = token.get("iss")
    if not isinstance(iss, str):
        raise CredentialAuthenticationError("credential missing issuer")
    secret = secrets_by_issuer.get(iss)
    if secret is None:
        raise CredentialAuthenticationError(f"no provisioned secret for issuer {iss!r}")
    try:
        claims = verify_credential(token, secret, now=now)
    except CredentialError as exc:
        raise CredentialAuthenticationError(str(exc)) from exc

    if claims.tenant is None:
        raise CredentialAuthenticationError("credential carries no workspace (tenant) claim")
    try:
        workspace_id = uuid.UUID(claims.tenant)
    except ValueError as exc:
        raise CredentialAuthenticationError("credential tenant is not a workspace id") from exc

    enrollments = (
        (
            await db.execute(
                select(StandaloneEnrollmentModel).where(
                    StandaloneEnrollmentModel.workspace_id == workspace_id,
                    StandaloneEnrollmentModel.deleted.is_(False),
                )
            )
        )
        .scalars()
        .all()
    )
    from hecate.execution.trust_roots import TrustRootNotFoundError, TrustRootRegistry

    root_registry = TrustRootRegistry(db)
    for enrollment in enrollments:
        host_ref = enrollment.host_identity_ref or {}
        if host_ref.get("issuer_domain") == claims.iss and host_ref.get("id") == claims.sub:
            if enrollment.admission is not AdmissionResult.ADMITTED or not enrollment.managed_new_runs:
                raise EnrollmentNotActiveError("enrollment is not admitted for managed new runs; new work is refused")
            # Reconnect re-verification (plan step7): the enrollment's trust
            # root must still resolve non-revoked — a revoked root refuses
            # every channel request, reconnect included.
            root_name = str(host_ref.get("name") or "")
            try:
                await root_registry.resolve(workspace_id, root_name)
            except TrustRootNotFoundError as exc:
                raise EnrollmentNotActiveError(
                    f"trust root {root_name!r} is revoked or unresolvable; re-verification failed"
                ) from exc
            return HostContext(
                workspace_id=workspace_id,
                enrollment_id=enrollment.id,
                deployment_domain=str(host_ref.get("name") or claims.iss),
                host_name=str(host_ref.get("id") or claims.sub),
                claims=claims,
                managed_scope=list(enrollment.managed_scope or []),
            )
    raise CredentialAuthenticationError("credential does not match an enrolled host identity")


def issue_lease_for(host: HostContext, secret: bytes, *, ttl_seconds: float) -> dict[str, Any]:
    """One authorization lease for the verified host (pull responses carry it).

    The lease carries the enrollment's managed scope: the operator-declared
    data domains protected actions may address until the next pull.
    """

    lease, _nonce = lease_claims(
        secret,
        iss=host.claims.iss,
        deployment_domain=host.deployment_domain,
        sub=host.claims.sub,
        ttl_seconds=ttl_seconds,
        scope=host.managed_scope,
    )
    return lease


def issue_channel_credential_for(
    secret: bytes,
    *,
    issuer_domain: str,
    host_id: str,
    workspace_id: uuid.UUID,
    ttl_seconds: float,
) -> dict[str, Any]:
    """One channel credential (registration success / rotation responses)."""

    token, _nonce = issue_credential(
        secret,
        iss=issuer_domain,
        sub=host_id,
        ttl_seconds=ttl_seconds,
        tenant=str(workspace_id),
    )
    return token


class ManagedDeliveryService:
    """Single writer of ``managed_deliveries`` (persisted dispatch intent)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def queue_delivery(
        self,
        *,
        workspace_id: uuid.UUID,
        enrollment_id: uuid.UUID,
        delivery_id: uuid.UUID,
        task_ref: dict[str, Any],
        run_ref: dict[str, Any],
        input_payload: dict[str, Any],
    ) -> ManagedDeliveryModel:
        row = ManagedDeliveryModel(
            enrollment_id=enrollment_id,
            workspace_id=workspace_id,
            task_ref=dict(task_ref),
            run_ref=dict(run_ref),
            input_payload=dict(input_payload),
            state=DELIVERY_PENDING,
        )
        self._session.add(row)
        await self._session.flush()
        # The row's id IS the delivery id used on the wire (cursor + dedup).
        row.delivery_ref = {"issuer_domain": "hecate", "id": str(row.id)}
        await self._session.flush()
        return row

    async def pull_batch(
        self,
        *,
        enrollment_id: uuid.UUID,
        cursor: str | None = None,
        limit: int = 50,
    ) -> list[ManagedDeliveryModel]:
        """Deliveries created after the cursor (ISO timestamp), oldest first.

        The cursor is best-effort batching; correctness comes from the
        host-side dedup (a redelivered row is answered from the idempotent
        accept record, never executed again).
        """

        stmt = select(ManagedDeliveryModel).where(
            ManagedDeliveryModel.enrollment_id == enrollment_id,
            ManagedDeliveryModel.deleted.is_(False),
            ManagedDeliveryModel.accepted_refs.is_(None),
        )
        # The timestamp cursor is advisory. An unacknowledged intent must
        # remain deliverable even after response loss or a late DB commit.
        if cursor and cursor != "0":
            _parse_cursor(cursor)
        return list(
            (
                await self._session.execute(
                    stmt.order_by(ManagedDeliveryModel.created_at, ManagedDeliveryModel.id).limit(limit)
                )
            )
            .scalars()
            .all()
        )

    async def accept(
        self,
        *,
        delivery_row_id: uuid.UUID,
        local_task_ref: dict[str, Any],
        local_run_ref: dict[str, Any],
    ) -> ManagedDeliveryModel:
        """Record the host's accept receipt; idempotent, conflict-rejecting.

        The same local references re-acknowledge harmlessly (lost response);
        different references for one delivery mean the host mapped it twice —
        rejected instead of overwritten.
        """

        row = await self._session.get(ManagedDeliveryModel, delivery_row_id)
        if row is None or row.deleted:
            raise ManagedChannelError(f"delivery {delivery_row_id} not found")
        if row.state == DELIVERY_DELIVERED:
            accepted = row.accepted_refs or {}
            if accepted.get("task_ref") != local_task_ref or accepted.get("run_ref") != local_run_ref:
                raise DeliveryConflictError(
                    f"delivery {delivery_row_id} is already accepted with different local references"
                )
            return row
        row.state = DELIVERY_DELIVERED
        row.accepted_refs = {"task_ref": dict(local_task_ref), "run_ref": dict(local_run_ref)}
        row.accepted_at = _utc_now()
        await self._session.flush()
        return row

    async def get_by_task_ref(self, workspace_id: uuid.UUID, task_ref: dict[str, Any]) -> ManagedDeliveryModel | None:
        """The delivery that carries one platform task (host mapping lookups)."""

        rows = (
            (
                await self._session.execute(
                    select(ManagedDeliveryModel).where(
                        ManagedDeliveryModel.workspace_id == workspace_id,
                        ManagedDeliveryModel.deleted.is_(False),
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            if row.task_ref.get("id") == task_ref.get("id") and row.task_ref.get("issuer_domain") == task_ref.get(
                "issuer_domain"
            ):
                return row
        return None


class ManagedProjectionService:
    """Project host-uploaded envelopes into the platform read model.

    Dedup is per event_id (the read model's unique index); the host's local
    task reference maps to the platform run through the delivery's accepted
    mapping, so the platform's own Run rows are updated, never the host's.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _find_accepted_delivery(
        self, host: HostContext, local_task_ref: dict[str, Any]
    ) -> ManagedDeliveryModel | None:
        """The delivery whose accepted mapping names the host's local task.

        Preview-profile lookup scoped to the enrollment (deliveries per host
        are few); an index follows the production profile.
        """

        rows = (
            (
                await self._session.execute(
                    select(ManagedDeliveryModel).where(
                        ManagedDeliveryModel.enrollment_id == host.enrollment_id,
                        ManagedDeliveryModel.deleted.is_(False),
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            accepted = row.accepted_refs or {}
            task_ref = accepted.get("task_ref") or {}
            if task_ref.get("id") == local_task_ref.get("id") and task_ref.get("issuer_domain") == local_task_ref.get(
                "issuer_domain"
            ):
                return row
        return None

    async def project_batch(
        self,
        host: HostContext,
        envelopes: list[dict[str, Any]],
        *,
        local_task_ref: dict[str, Any],
    ) -> dict[str, Any]:
        from hecate.execution.governance_events import PlatformEventService

        delivery = await self._find_accepted_delivery(host, local_task_ref)
        if delivery is None or delivery.accepted_refs is None:
            raise ManagedChannelError("no accepted delivery maps the host's local task reference")

        projected, skipped = 0, 0
        fresh: list[dict[str, Any]] = []
        events = PlatformEventService(self._session)
        platform_run_id = uuid.UUID(delivery.run_ref["id"]) if delivery.run_ref.get("id") else None
        for envelope in envelopes:
            if envelope.get("task_ref") != delivery.accepted_refs.get("task_ref") or envelope.get(
                "run_ref"
            ) != delivery.accepted_refs.get("run_ref"):
                raise ManagedChannelError("event task/run does not match the accepted delivery")
            from hecate.contracts.execution.events import EventEnvelope, validate_governance_event

            try:
                validate_governance_event(EventEnvelope.from_dict(envelope))
            except (ValueError, KeyError, TypeError) as exc:
                raise ManagedChannelError("invalid governance event envelope") from exc
            if envelope.get("event_id") is None:
                skipped += 1
                continue
            stored = _rebind_envelope(envelope, platform_run_id=platform_run_id)
            existing = await events.find_by_event_id(envelope["event_id"])
            if existing is not None:
                expected_digest = (existing.extra.get("detail_ns") or {}).get("host_origin", {}).get("event_digest")
                if (
                    existing.run_ref != stored.run_ref
                    or existing.task_ref != stored.task_ref
                    or existing.payload != stored.payload
                    or existing.actor != stored.actor
                    or existing.source != stored.source
                    or (
                        expected_digest is not None
                        and expected_digest != stored.extra["detail_ns"]["host_origin"]["event_digest"]
                    )
                ):
                    raise ManagedChannelError("event_id replay conflicts with recorded host facts")
                skipped += 1
                continue
            await events.append_resequenced(stored, workspace_id=host.workspace_id)
            fresh.append(envelope)
            projected += 1
        await self._update_run_projection(host, local_task_ref, fresh)
        await self._session.commit()
        return {"projected": projected, "skipped_duplicates": skipped}

    async def _update_run_projection(
        self, host: HostContext, local_task_ref: dict[str, Any], envelopes: list[dict[str, Any]]
    ) -> None:
        """Fold the host's latest task_state/run_terminal payloads into the Run projection."""

        delivery = await self._find_accepted_delivery(host, local_task_ref)
        if delivery is None or delivery.run_ref.get("id") is None:
            return
        from hecate.execution.task_run_registry import TaskRunRegistry
        from hecate.models.run import RunModel

        run = (
            await self._session.execute(
                select(RunModel)
                .where(
                    RunModel.id == uuid.UUID(delivery.run_ref["id"]),
                    RunModel.workspace_id == host.workspace_id,
                    RunModel.deleted.is_(False),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if run is None:
            return
        merged = dict(run.projection or {})
        cursors = dict(merged.get("source_cursors") or {})
        for envelope in sorted(envelopes, key=lambda e: e["source_sequence"]):
            source = str(envelope.get("source"))
            sequence = envelope["source_sequence"]
            if sequence <= cursors.get(source, 0):
                continue
            cursors[source] = sequence
            payload = envelope.get("payload") or {}
            event_type = payload.get("event_type")
            if merged.get("state") in {"succeeded", "failed", "cancelled"} and not (
                event_type == "run_terminal" and payload.get("status") == merged.get("state")
            ):
                continue  # late/replayed observations never reopen a terminal run
            if event_type == "task_state":
                merged["state"] = payload.get("state")
            elif event_type == "run_terminal":
                merged.update(
                    {
                        "state": payload.get("status", "unknown"),
                        "error": payload.get("error"),
                        "result_preview": str(payload.get("content") or "")[:2000],
                    }
                )
        merged["source_cursors"] = cursors
        await TaskRunRegistry(self._session).update_projection(run.id, host.workspace_id, projection=merged)


def _rebind_envelope(envelope: dict[str, Any], *, platform_run_id: uuid.UUID | None):
    """Rebind one host envelope to the platform run for read-model storage."""

    from dataclasses import replace

    from hecate.contracts.execution.durable import canonical_request_digest
    from hecate.contracts.execution.events import EventEnvelope
    from hecate.contracts.execution.references import run_ref as mk_run_ref
    from hecate.contracts.execution.references import task_ref as mk_task_ref

    envelope = dict(envelope)
    task_part = envelope.get("task_ref") or {}
    run_part = envelope.get("run_ref") or {}
    new_task = mk_task_ref(str(task_part.get("issuer_domain") or "host"), str(task_part.get("id") or ""))
    new_run = (
        mk_run_ref(str(run_part.get("issuer_domain") or "host"), str(run_part.get("id") or ""))
        if platform_run_id is None
        else mk_run_ref("hecate", str(platform_run_id))
    )
    stored = EventEnvelope.from_dict(envelope)
    return replace(
        stored,
        task_ref=new_task,
        run_ref=new_run,
        source_sequence=stored.source_sequence,
        extra={
            **stored.extra,
            "detail_ns": {
                **(stored.extra.get("detail_ns") or {}),
                "host_origin": {
                    "source_sequence": stored.source_sequence,
                    "event_digest": canonical_request_digest({k: v for k, v in envelope.items() if k != "received_at"}),
                },
            },
        },
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _parse_cursor(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ManagedChannelError(f"unparsable pull cursor {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed
