"""Receive a signed Slack event into an isolated, observe-only ledger."""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import ClientDisconnect

from hecate.channel.slack_signature import verify_slack_signature
from hecate.core.auth_context import AuthContext
from hecate.core.config import settings
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.models.slack_events import SlackEventObservationModel
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceRole

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/slack-events", tags=["slack-events"])

DISPOSITION_OBSERVED = "observed"
DISPOSITION_DUPLICATE = "duplicate"
_SOURCE = "slack"
_OBSERVATION_KEY = (
    SlackEventObservationModel.workspace_id,
    SlackEventObservationModel.source,
    SlackEventObservationModel.event_id,
)


def _bound_workspace_id() -> uuid.UUID | None:
    """Return a valid server-side workspace binding, if configured."""
    try:
        return uuid.UUID(settings.SLACK_EVENT_OBSERVATION_WORKSPACE_ID)
    except ValueError:
        return None


def _matches_id(value: object, pattern: str) -> bool:
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def _profile_is_configured() -> bool:
    return (
        bool(settings.SLACK_EVENT_OBSERVATION_SIGNING_SECRET.get_secret_value())
        and _matches_id(settings.SLACK_EVENT_OBSERVATION_APP_ID, r"A[A-Z0-9]{1,63}")
        and _matches_id(settings.SLACK_EVENT_OBSERVATION_TEAM_ID, r"T[A-Z0-9]{1,63}")
        and _matches_id(settings.SLACK_EVENT_OBSERVATION_CHANNEL_ID, r"[CG][A-Z0-9]{1,63}")
    )


async def _workspace_exists(db: AsyncSession, workspace_id: uuid.UUID) -> bool:
    stmt = select(WorkspaceModel.id).where(WorkspaceModel.id == workspace_id, WorkspaceModel.deleted.is_(False))
    return await db.scalar(stmt) is not None


async def _refusal(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    disposition: str,
    *,
    http_status: int,
) -> JSONResponse:
    """Count a signed refusal in a bounded bucket, without storing payload fields."""
    limit = settings.SLACK_EVENT_OBSERVATION_REJECT_LOG_LIMIT
    if limit:
        bucket = int(datetime.now(UTC).timestamp()) // settings.SLACK_EVENT_OBSERVATION_REJECT_LOG_WINDOW_SECONDS
        insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
        stmt = insert(SlackEventObservationModel).values(
            source=_SOURCE,
            workspace_id=workspace_id,
            event_id=f"refusal:{bucket}:{disposition}",
            disposition=disposition,
        )
        try:
            await db.execute(
                stmt.on_conflict_do_update(
                    index_elements=_OBSERVATION_KEY,
                    set_={"delivery_count": SlackEventObservationModel.delivery_count + 1, "received_at": func.now()},
                    where=SlackEventObservationModel.delivery_count < limit,
                )
            )
            await db.commit()
        except SQLAlchemyError:
            await db.rollback()
            logger.warning("slack-events: refusal counter unavailable")
    if http_status == 200:
        return JSONResponse({"ok": True, "observation": disposition})
    return JSONResponse({"error": disposition}, status_code=http_status)


@router.post("")
async def observe_slack_event(request: Request, db: Annotated[AsyncSession, Depends(get_db)]) -> JSONResponse:
    """Receive signed Slack callbacks and confirm events after the ledger commit."""
    if not settings.SLACK_EVENT_OBSERVATION_ENABLED:
        return JSONResponse({"error": "not found"}, status_code=404)

    workspace_id = _bound_workspace_id()
    if (
        workspace_id is None
        or not _profile_is_configured()
        or db.get_bind().dialect.name not in {"postgresql", "sqlite"}
    ):
        return JSONResponse({"error": "observation entry misconfigured"}, status_code=503)

    max_bytes = settings.SLACK_EVENT_OBSERVATION_MAX_BODY_BYTES
    raw_body = bytearray()
    try:
        content_length = int(request.headers.get("content-length", "0"))
        if content_length < 0:
            raise ValueError("negative content length")
        if content_length > max_bytes:
            return JSONResponse({"error": "payload too large"}, status_code=413)
        async for chunk in request.stream():
            if len(raw_body) + len(chunk) > max_bytes:
                return JSONResponse({"error": "payload too large"}, status_code=413)
            raw_body.extend(chunk)
    except (ClientDisconnect, ValueError):
        return JSONResponse({"error": "invalid request body"}, status_code=400)

    verify_status, verify_body = verify_slack_signature(
        settings.SLACK_EVENT_OBSERVATION_SIGNING_SECRET.get_secret_value(), request.headers, bytes(raw_body)
    )
    if verify_status != 200:
        return JSONResponse(verify_body, status_code=verify_status)

    try:
        if not await _workspace_exists(db, workspace_id):
            return JSONResponse({"error": "observation entry misconfigured"}, status_code=503)
        return await _handle_verified_request(db, workspace_id, raw_body)
    except SQLAlchemyError:
        await db.rollback()
        logger.warning("slack-events: ledger unavailable")
        return JSONResponse({"error": "ledger unavailable"}, status_code=500)


async def _handle_verified_request(db: AsyncSession, workspace_id: uuid.UUID, raw_body: bytearray) -> JSONResponse:
    try:
        payload = json.loads(raw_body)
    except (ValueError, RecursionError):
        payload = None
    if not isinstance(payload, dict):
        return await _refusal(db, workspace_id, "rejected_malformed", http_status=400)

    payload_type = payload.get("type")
    if payload_type == "url_verification":
        # Slack's challenge carries no app/team IDs. The dedicated signing key
        # authenticates it without treating any payload field as a credential.
        challenge = payload.get("challenge")
        if not _matches_id(challenge, r"[A-Za-z0-9_-]{1,256}"):
            return JSONResponse({"error": "invalid challenge"}, status_code=400)
        return JSONResponse({"challenge": challenge})
    if payload_type != "event_callback":
        return await _refusal(db, workspace_id, "rejected_payload_type", http_status=400)

    return await _handle_event_callback(db, workspace_id, payload)


async def _handle_event_callback(db: AsyncSession, workspace_id: uuid.UUID, payload: dict[str, Any]) -> JSONResponse:
    event = payload.get("event")
    event_id = payload.get("event_id")
    if not isinstance(event, dict) or not _matches_id(event_id, r"Ev[A-Za-z0-9]{1,126}"):
        return await _refusal(db, workspace_id, "rejected_malformed", http_status=400)

    app_id = settings.SLACK_EVENT_OBSERVATION_APP_ID
    team_id = settings.SLACK_EVENT_OBSERVATION_TEAM_ID
    channel_id = settings.SLACK_EVENT_OBSERVATION_CHANNEL_ID
    if (
        payload.get("api_app_id") != app_id
        or payload.get("team_id") != team_id
        or payload.get("team", team_id) != team_id
        or payload.get("is_ext_shared_channel")
        or event.get("team", team_id) != team_id
        or event.get("channel") != channel_id
    ):
        return await _refusal(db, workspace_id, "rejected_foreign_source", http_status=403)
    if event.get("type") != "app_mention":
        return await _refusal(db, workspace_id, "rejected_event_type", http_status=403)
    if event.get("bot_id") or event.get("bot_profile") or event.get("subtype") == "bot_message":
        return await _refusal(db, workspace_id, "rejected_bot_event", http_status=200)
    if event.get("subtype") or not _matches_id(event.get("user"), r"[UW][A-Z0-9]{1,63}"):
        return await _refusal(db, workspace_id, "rejected_malformed", http_status=400)

    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    stmt = insert(SlackEventObservationModel).values(
        source=_SOURCE,
        workspace_id=workspace_id,
        app_id=app_id,
        team_id=team_id,
        channel_id=channel_id,
        event_id=event_id,
        event_type="app_mention",
        disposition=DISPOSITION_OBSERVED,
    )
    inserted = await db.scalar(
        stmt.on_conflict_do_nothing(index_elements=_OBSERVATION_KEY).returning(SlackEventObservationModel.id)
    )
    await db.commit()
    return JSONResponse({"ok": True, "observation": DISPOSITION_OBSERVED if inserted else DISPOSITION_DUPLICATE})


@router.get("/observations/summary")
async def observation_summary(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Read observation counts as an admin of the bound workspace."""
    bound = _bound_workspace_id()
    if bound is None or ctx.workspace_id != bound:
        raise HTTPException(status_code=404, detail="observation summary unavailable")
    if ctx.auth_method != "jwt" or ctx.role != WorkspaceRole.ADMIN:
        raise HTTPException(status_code=403, detail="workspace admin required")

    rows = await db.execute(
        select(
            SlackEventObservationModel.disposition,
            func.sum(SlackEventObservationModel.delivery_count),
            func.max(SlackEventObservationModel.received_at),
        )
        .where(SlackEventObservationModel.workspace_id == bound, SlackEventObservationModel.deleted.is_(False))
        .group_by(SlackEventObservationModel.disposition)
    )
    counts = {}
    latest = None
    for disposition, count, received_at in rows:
        counts[disposition] = count
        if latest is None or received_at > latest:
            latest = received_at
    return {
        "enabled": settings.SLACK_EVENT_OBSERVATION_ENABLED,
        "source": _SOURCE,
        "workspace_id": str(bound),
        "counts": counts,
        "latest_received_at": latest.isoformat() if latest is not None else None,
    }


async def purge_expired_observations(db: AsyncSession, *, now: datetime | None = None) -> int:
    """Delete ledger rows older than the deploy-time retention window."""
    cutoff = (now or datetime.now(UTC)) - timedelta(days=settings.SLACK_EVENT_OBSERVATION_RETENTION_DAYS)
    result = await db.execute(delete(SlackEventObservationModel).where(SlackEventObservationModel.created_at < cutoff))
    await db.commit()
    return result.rowcount or 0
