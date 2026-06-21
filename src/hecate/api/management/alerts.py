"""API routers for alert management: rules, events, silences, channels, policies."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.models.alert import (
    AlertRuleCreateSchema,
    AlertRuleReadSchema,
    AlertRuleUpdateSchema,
    AlertSilenceCreateSchema,
    AlertSilenceReadSchema,
    EscalationPolicyCreateSchema,
    EscalationPolicyReadSchema,
    EscalationPolicyUpdateSchema,
    NotificationChannelCreateSchema,
    NotificationChannelReadSchema,
    NotificationChannelUpdateSchema,
)
from hecate.services.alert_service import AlertService
from hecate.services.notification_dispatcher import NotificationDispatcher

rules_router = APIRouter(tags=["alerts"])
events_router = APIRouter(tags=["alerts"])
silences_router = APIRouter(tags=["alerts"])
channels_router = APIRouter(tags=["alerts"])
escalation_policies_router = APIRouter(tags=["alerts"])


# --- Alert Rules ---


@rules_router.post("/alerts/rules", response_model=AlertRuleReadSchema, status_code=201)
async def create_rule(
    body: AlertRuleCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> AlertRuleReadSchema:
    """Create a new alert rule."""
    service = AlertService(db, workspace_id=ctx.workspace_id)
    rule = await service.create_rule(**body.model_dump())
    await db.commit()
    return AlertRuleReadSchema.model_validate(rule)


@rules_router.get("/alerts/rules", response_model=list[AlertRuleReadSchema])
async def list_rules(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    enabled_only: Annotated[bool, Query()] = False,
) -> list[AlertRuleReadSchema]:
    """List alert rules for the current workspace."""
    service = AlertService(db, workspace_id=ctx.workspace_id)
    rules = await service.list_rules(enabled_only=enabled_only)
    return [AlertRuleReadSchema.model_validate(r) for r in rules]


@rules_router.get("/alerts/rules/{rule_id}", response_model=AlertRuleReadSchema)
async def get_rule(
    rule_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> AlertRuleReadSchema:
    """Get a single alert rule."""
    service = AlertService(db, workspace_id=ctx.workspace_id)
    rule = await service.get_rule(rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    return AlertRuleReadSchema.model_validate(rule)


@rules_router.put("/alerts/rules/{rule_id}", response_model=AlertRuleReadSchema)
async def update_rule(
    rule_id: uuid.UUID,
    body: AlertRuleUpdateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> AlertRuleReadSchema:
    """Update an alert rule."""
    service = AlertService(db, workspace_id=ctx.workspace_id)
    rule = await service.update_rule(rule_id, **body.model_dump(exclude_unset=True))
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    await db.commit()
    return AlertRuleReadSchema.model_validate(rule)


@rules_router.delete("/alerts/rules/{rule_id}", status_code=204)
async def delete_rule(
    rule_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> None:
    """Soft-delete an alert rule."""
    service = AlertService(db, workspace_id=ctx.workspace_id)
    if not await service.delete_rule(rule_id):
        raise HTTPException(status_code=404, detail="Rule not found")
    await db.commit()


# --- Alert Events ---


@events_router.get("/alerts/events", response_model=list)
async def list_events(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    state: Annotated[str | None, Query()] = None,
    rule_id: Annotated[uuid.UUID | None, Query()] = None,
) -> list[dict]:
    """List alert events with optional filters."""
    service = AlertService(db, workspace_id=ctx.workspace_id)
    events = await service.list_events(state=state, rule_id=rule_id)
    return [
        {
            "id": str(e.id),
            "rule_id": str(e.rule_id),
            "state": e.state,
            "current_value": e.current_value,
            "fired_at": e.fired_at.isoformat() if e.fired_at else None,
            "resolved_at": e.resolved_at.isoformat() if e.resolved_at else None,
            "acked_at": e.acked_at.isoformat() if e.acked_at else None,
            "escalation_step": e.escalation_step,
            "created_at": e.created_at.isoformat(),
        }
        for e in events
    ]


@events_router.post("/alerts/events/{event_id}/ack")
async def acknowledge_event(
    event_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Acknowledge an alert event."""
    service = AlertService(db, workspace_id=ctx.workspace_id)
    user_id = getattr(ctx, "user_id", None) or uuid.UUID(int=0)
    event = await service.acknowledge_event(event_id, user_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    await db.commit()
    return {"id": str(event.id), "state": event.state, "acked_at": event.acked_at.isoformat()}


# --- Silences ---


@silences_router.post("/alerts/silences", response_model=AlertSilenceReadSchema, status_code=201)
async def create_silence(
    body: AlertSilenceCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> AlertSilenceReadSchema:
    """Create a silence window."""
    service = AlertService(db, workspace_id=ctx.workspace_id)
    silence = await service.create_silence(**body.model_dump())
    await db.commit()
    return AlertSilenceReadSchema.model_validate(silence)


@silences_router.get("/alerts/silences", response_model=list[AlertSilenceReadSchema])
async def list_silences(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    active: Annotated[bool, Query()] = False,
) -> list[AlertSilenceReadSchema]:
    """List silence windows."""
    service = AlertService(db, workspace_id=ctx.workspace_id)
    silences = await service.list_silences(active_only=active)
    return [AlertSilenceReadSchema.model_validate(s) for s in silences]


@silences_router.delete("/alerts/silences/{silence_id}", status_code=204)
async def delete_silence(
    silence_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> None:
    """Delete a silence window."""
    service = AlertService(db, workspace_id=ctx.workspace_id)
    if not await service.delete_silence(silence_id):
        raise HTTPException(status_code=404, detail="Silence not found")
    await db.commit()


# --- Notification Channels ---


@channels_router.post("/alerts/channels", response_model=NotificationChannelReadSchema, status_code=201)
async def create_channel(
    body: NotificationChannelCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> NotificationChannelReadSchema:
    """Create a notification channel."""
    service = AlertService(db, workspace_id=ctx.workspace_id)
    channel = await service.create_channel(**body.model_dump())
    await db.commit()
    return NotificationChannelReadSchema.model_validate(channel)


@channels_router.get("/alerts/channels", response_model=list[NotificationChannelReadSchema])
async def list_channels(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> list[NotificationChannelReadSchema]:
    """List notification channels."""
    service = AlertService(db, workspace_id=ctx.workspace_id)
    channels = await service.list_channels()
    return [NotificationChannelReadSchema.model_validate(ch) for ch in channels]


@channels_router.put("/alerts/channels/{channel_id}", response_model=NotificationChannelReadSchema)
async def update_channel(
    channel_id: uuid.UUID,
    body: NotificationChannelUpdateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> NotificationChannelReadSchema:
    """Update a notification channel."""
    service = AlertService(db, workspace_id=ctx.workspace_id)
    channel = await service.update_channel(channel_id, **body.model_dump(exclude_unset=True))
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    await db.commit()
    return NotificationChannelReadSchema.model_validate(channel)


@channels_router.delete("/alerts/channels/{channel_id}", status_code=204)
async def delete_channel(
    channel_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> None:
    """Delete a notification channel."""
    service = AlertService(db, workspace_id=ctx.workspace_id)
    if not await service.delete_channel(channel_id):
        raise HTTPException(status_code=404, detail="Channel not found")
    await db.commit()


@channels_router.post("/alerts/channels/{channel_id}/test")
async def test_channel(
    channel_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Test a notification channel by sending a test alert."""
    service = AlertService(db, workspace_id=ctx.workspace_id)
    channel = await service.get_channel(channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")

    from hecate.models.alert import AlertEventModel, AlertRuleModel

    fake_rule = AlertRuleModel(
        name="Test Alert",
        alert_type="error_rate",
        threshold=0.0,
        severity="info",
        window_minutes=1,
        for_minutes=0,
    )
    fake_event = AlertEventModel(
        rule_id=uuid.UUID(int=0),
        state="firing",
        current_value=1.0,
        fired_at=datetime.now(UTC),
    )
    dispatcher = NotificationDispatcher()
    results = await dispatcher.dispatch(fake_event, fake_rule, [channel])
    return {"results": results}


# --- Escalation Policies ---


@escalation_policies_router.post(
    "/alerts/escalation-policies", response_model=EscalationPolicyReadSchema, status_code=201
)
async def create_policy(
    body: EscalationPolicyCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> EscalationPolicyReadSchema:
    """Create an escalation policy."""
    service = AlertService(db, workspace_id=ctx.workspace_id)
    policy = await service.create_policy(**body.model_dump())
    await db.commit()
    return EscalationPolicyReadSchema.model_validate(policy)


@escalation_policies_router.get("/alerts/escalation-policies", response_model=list[EscalationPolicyReadSchema])
async def list_policies(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> list[EscalationPolicyReadSchema]:
    """List escalation policies."""
    service = AlertService(db, workspace_id=ctx.workspace_id)
    policies = await service.list_policies()
    return [EscalationPolicyReadSchema.model_validate(p) for p in policies]


@escalation_policies_router.put("/alerts/escalation-policies/{policy_id}", response_model=EscalationPolicyReadSchema)
async def update_policy(
    policy_id: uuid.UUID,
    body: EscalationPolicyUpdateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> EscalationPolicyReadSchema:
    """Update an escalation policy."""
    service = AlertService(db, workspace_id=ctx.workspace_id)
    policy = await service.update_policy(policy_id, **body.model_dump(exclude_unset=True))
    if policy is None:
        raise HTTPException(status_code=404, detail="Policy not found")
    await db.commit()
    return EscalationPolicyReadSchema.model_validate(policy)


@escalation_policies_router.delete("/alerts/escalation-policies/{policy_id}", status_code=204)
async def delete_policy(
    policy_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> None:
    """Delete an escalation policy."""
    service = AlertService(db, workspace_id=ctx.workspace_id)
    if not await service.delete_policy(policy_id):
        raise HTTPException(status_code=404, detail="Policy not found")
    await db.commit()
