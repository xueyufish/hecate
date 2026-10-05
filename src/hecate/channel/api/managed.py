"""Managed runner channel API (step6/7 managed slice).

Two route families:

- **operator routes** (``/managed/enrollments``) — workspace-admin
  authenticated via the standard session/API-key dependency: registration
  review, admission + managed-new-runs opt-in, trust-root provisioning.
- **host channel routes** (``/managed/host/*``) — authenticated by the
  deployment-domain HMAC credential (NOT user sessions): challenge,
  register, pull (deliveries cursor + fresh lease), accept, event upload.
  Client-asserted fields are ignored; authorization is the verified
  credential bound to an admitted enrollment.

Channel credentials are verified against operator-provisioned secrets held
in :mod:`hecate.core.composition.managed_secrets` (issuer domain -> raw
material; the platform stores digests only).
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context

if TYPE_CHECKING:
    # Execution-domain types are annotation-only here; runtime imports stay
    # function-local (channel/ must not import other domains at module level
    # - the same lazy-import pattern as the task control API).
    from hecate.execution.managed_channel import HostContext, ManagedChannelError

router = APIRouter(prefix="/managed", tags=["managed"])

# Lease TTL bound to the pull cadence: the plan's "maximum stale window" is
# this value, explicit in the managed profile (design D4).
DEFAULT_LEASE_TTL_SECONDS = 120.0
DEFAULT_CHANNEL_CREDENTIAL_TTL_SECONDS = 3600.0


def _secrets() -> dict[str, bytes]:
    from hecate.core.composition.managed_secrets import get_managed_secrets

    return get_managed_secrets()


def _host_credential_token(authorization: str | None) -> dict[str, Any] | None:
    if not authorization or not authorization.startswith("ManagedBearer "):
        return None
    try:
        return json.loads(authorization[len("ManagedBearer ") :])
    except ValueError:
        return None


async def _host_context(
    authorization: str | None,
    db: AsyncSession,
) -> HostContext:
    from hecate.execution.managed_channel import (
        CredentialAuthenticationError,
        authenticate_host,
    )

    token = _host_credential_token(authorization)
    if token is None:
        raise CredentialAuthenticationError("missing ManagedBearer credential")
    return await authenticate_host(db, token, secrets_by_issuer=_secrets())


def _host_error(exc: ManagedChannelError) -> HTTPException:
    from hecate.execution.managed_channel import (
        CredentialAuthenticationError,
        DeliveryConflictError,
        EnrollmentNotActiveError,
    )

    if isinstance(exc, CredentialAuthenticationError):
        return HTTPException(status_code=401, detail={"error": {"code": "unauthenticated", "message": str(exc)}})
    if isinstance(exc, EnrollmentNotActiveError):
        return HTTPException(status_code=403, detail={"error": {"code": "enrollment-inactive", "message": str(exc)}})
    if isinstance(exc, DeliveryConflictError):
        return HTTPException(status_code=409, detail={"error": {"code": "conflict", "message": str(exc)}})
    return HTTPException(status_code=422, detail={"error": {"code": "invalid-request", "message": str(exc)}})


HostAuth = Annotated[str | None, Query()]


class ChallengeResponse(BaseModel):
    nonce: str


class RegisterRequest(BaseModel):
    workspace_id: uuid.UUID
    host_identity_ref: dict[str, Any]
    trust_root_ref: dict[str, Any]
    installed_versions: dict[str, Any] = Field(default_factory=dict)


class OptInRequest(BaseModel):
    managed_new_runs: bool
    admitted: bool


class TrustRootRequest(BaseModel):
    name: str
    material_digest: str
    issuer_domain: str
    config_fingerprint: str
    meta: dict[str, Any] = Field(default_factory=dict)


class AcceptRequest(BaseModel):
    delivery_row_id: uuid.UUID
    local_task_ref: dict[str, Any]
    local_run_ref: dict[str, Any]


class EventsUploadRequest(BaseModel):
    local_task_ref: dict[str, Any]
    envelopes: list[dict[str, Any]]


# --- operator routes (workspace-admin session auth) -------------------------


@router.get("/challenge", response_model=ChallengeResponse)
async def challenge() -> ChallengeResponse:
    """Registration challenge: the host answers with HMAC(secret, nonce)."""

    from hecate.execution.managed_credentials import new_nonce

    return ChallengeResponse(nonce=new_nonce())


@router.post("/enrollments")
async def register_enrollment(
    body: RegisterRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    auth: Annotated[AuthContext, Depends(get_auth_context)],
):
    """Record one enrollment request (pending; managed_new_runs false)."""
    from hecate.execution.task_run_registry import TaskRunRegistry, TaskRunValidationError

    _ = auth  # session-authenticated operator surface
    try:
        enrollment = await TaskRunRegistry(db).register_standalone_enrollment(
            workspace_id=body.workspace_id,
            host_identity_ref=body.host_identity_ref,
            trust_root_ref=body.trust_root_ref,
            installed_versions=body.installed_versions,
        )
        await db.commit()
    except TaskRunValidationError as exc:
        raise HTTPException(
            status_code=422, detail={"error": {"code": "invalid-request", "message": str(exc)}}
        ) from exc
    return JSONResponse(
        status_code=201,
        content={
            "enrollment_id": str(enrollment.id),
            "admission": enrollment.admission.value,
            "managed_new_runs": enrollment.managed_new_runs,
        },
    )


@router.post("/enrollments/{enrollment_id}/opt-in")
async def set_opt_in(
    enrollment_id: uuid.UUID,
    body: OptInRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    auth: Annotated[AuthContext, Depends(get_auth_context)],
):
    """Operator-reviewed admission + managed opt-in (workspace admin only)."""
    from hecate.execution.enrollment_resolver import HmacEnrollmentResolver
    from hecate.execution.task_run_registry import TaskRunRegistry, TaskRunValidationError

    resolver = HmacEnrollmentResolver(db, secret_candidates=_secrets())
    try:
        row = await TaskRunRegistry(db, enrollment_resolver=resolver.verify).set_managed_opt_in(
            enrollment_id,
            auth.workspace_id,
            managed_new_runs=body.managed_new_runs,
            operator_id=auth.user_id,
            admitted=body.admitted,
        )
        await db.commit()
    except TaskRunValidationError as exc:
        raise HTTPException(
            status_code=422, detail={"error": {"code": "admission-rejected", "message": str(exc)}}
        ) from exc
    return {"enrollment_id": str(row.id), "admission": row.admission.value, "managed_new_runs": row.managed_new_runs}


@router.post("/trust-roots")
async def register_trust_root(
    body: TrustRootRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    auth: Annotated[AuthContext, Depends(get_auth_context)],
):
    """Provision one workspace trust material (digest-only storage)."""
    from hecate.execution.trust_roots import TrustRootError, TrustRootRegistry

    registry = TrustRootRegistry(db)
    try:
        row = await registry.register(
            workspace_id=auth.workspace_id,
            name=body.name,
            material_digest=body.material_digest,
            issuer_domain=body.issuer_domain,
            config_fingerprint=body.config_fingerprint,
            meta=body.meta,
        )
        await db.commit()
    except TrustRootError as exc:
        raise HTTPException(
            status_code=422, detail={"error": {"code": "invalid-request", "message": str(exc)}}
        ) from exc
    return {"name": row.name, "issuer_domain": row.issuer_domain, "revoked": row.revoked}


@router.post("/trust-roots/{name}/revoke")
async def revoke_trust_root(
    name: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    auth: Annotated[AuthContext, Depends(get_auth_context)],
):
    """Revoke one trust material: resolution and reconnect verification stop."""
    from hecate.execution.trust_roots import TrustRootError, TrustRootRegistry

    registry = TrustRootRegistry(db)
    try:
        await registry.revoke(auth.workspace_id, name)
        await db.commit()
    except TrustRootError as exc:
        raise HTTPException(status_code=404, detail={"error": {"code": "not-found", "message": str(exc)}}) from exc
    return {"name": name, "revoked": True}


# --- host channel routes (ManagedBearer credential auth) ---------------------


async def _authenticated_host(
    db: Annotated[AsyncSession, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
) -> HostContext:
    from hecate.execution.managed_channel import ManagedChannelError

    try:
        return await _host_context(authorization, db)
    except ManagedChannelError as exc:
        raise _host_error(exc) from exc


# FastAPI only needs the dependency here; the host context type stays lazy
# (TYPE_CHECKING-only, per the channel layering rule).
HostDep = Annotated[Any, Depends(_authenticated_host)]


@router.post("/host/credential")
async def host_credential(
    body: dict[str, Any],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Exchange a challenge answer for a channel credential.

    The body names the trust root and carries the challenge answer; the
    platform verifies possession against the provisioned secret and issues
    a deployment-domain credential scoped to the host's workspace. Admission
    is NOT granted here — credentials authenticate, the operator gate
    authorizes.
    """

    try:
        workspace_id = uuid.UUID(str(body.get("workspace_id")))
    except ValueError:
        raise HTTPException(
            status_code=422, detail={"error": {"code": "invalid-request", "message": "workspace_id must be a uuid"}}
        ) from None
    root_name = str(body.get("trust_root") or "")
    answer = str(body.get("answer") or "")
    host_id = str(body.get("host_id") or "")
    if not root_name or not answer or not host_id:
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": "invalid-request", "message": "trust_root, answer, and host_id are required"}},
        )
    # Preview-profile secret lookup: provisioned materials are few, so the
    # challenge is verified against each candidate and the verifying secret
    # is then bound to the named root via its registered digest — a root
    # whose digest matches a different candidate cannot be impersonated.
    from hecate.execution.managed_channel import issue_channel_credential_for
    from hecate.execution.managed_credentials import material_digest, verify_challenge
    from hecate.execution.trust_roots import TrustRootRegistry

    registry = TrustRootRegistry(db)
    root = None
    secret = None
    for candidate in _secrets().values():
        if not verify_challenge(candidate, str(body.get("nonce") or ""), answer):
            continue
        peeked = await registry.peek(workspace_id, root_name)
        if peeked is not None and peeked.material_digest == material_digest(candidate):
            root, secret = peeked, candidate
            break
    if root is None or secret is None:
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": "unauthenticated",
                    "message": "challenge answer does not verify against the named trust root",
                }
            },
        )
    token = issue_channel_credential_for(
        secret,
        issuer_domain=root.issuer_domain,
        host_id=host_id,
        workspace_id=root.workspace_id,
        ttl_seconds=DEFAULT_CHANNEL_CREDENTIAL_TTL_SECONDS,
    )
    return {"credential": token, "token_type": "ManagedBearer"}


@router.get("/host/pull")
async def host_pull(
    db: Annotated[AsyncSession, Depends(get_db)],
    host: HostDep,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
):
    """Pull deliveries after the cursor; the response carries a fresh lease.

    Delivered rows within the window are redelivered on purpose — the host
    answers them from its idempotent accept record (reconciliation after a
    lost response), never by executing again.
    """

    from hecate.execution.managed_channel import ManagedDeliveryService, issue_lease_for

    service = ManagedDeliveryService(db)
    rows = await service.pull_batch(enrollment_id=host.enrollment_id, cursor=cursor or None, limit=limit)
    lease = issue_lease_for(host, _secrets()[host.claims.iss], ttl_seconds=DEFAULT_LEASE_TTL_SECONDS)
    next_cursor = rows[-1].created_at.isoformat() if rows else cursor
    return {
        "deliveries": [
            {
                "delivery_row_id": str(row.id),
                "state": row.state,
                "task_ref": row.task_ref,
                "run_ref": row.run_ref,
                "input_payload": row.input_payload,
            }
            for row in rows
        ],
        "next_cursor": next_cursor,
        "lease": lease,
    }


@router.post("/host/accept")
async def host_accept(
    body: AcceptRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    host: HostDep,
):
    """Record the host's accept receipt (idempotent; conflicting refs 409)."""

    from hecate.execution.managed_channel import (
        DeliveryConflictError,
        ManagedChannelError,
        ManagedDeliveryService,
    )

    service = ManagedDeliveryService(db)
    try:
        row = await service.accept(
            delivery_row_id=body.delivery_row_id,
            local_task_ref=body.local_task_ref,
            local_run_ref=body.local_run_ref,
        )
        await db.commit()
    except DeliveryConflictError as exc:
        raise _host_error(exc) from exc
    except ManagedChannelError as exc:
        raise _host_error(exc) from exc
    return {"delivery_row_id": row.id, "state": row.state}


@router.post("/host/events")
async def host_events(
    body: EventsUploadRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    host: HostDep,
):
    """Upload durable-log envelopes; dedup is per event_id (replays harmless)."""

    from hecate.execution.managed_channel import ManagedChannelError, ManagedProjectionService

    service = ManagedProjectionService(db)
    try:
        result = await service.project_batch(host, body.envelopes, local_task_ref=body.local_task_ref)
    except ManagedChannelError as exc:
        await db.rollback()
        raise _host_error(exc) from exc
    return result
