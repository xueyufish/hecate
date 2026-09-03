"""Business logic for alert rules, events, silences, escalation policies, and channels."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.alert import (
    AlertEventModel,
    AlertRuleModel,
    AlertSilenceModel,
    AlertState,
    EscalationPolicyModel,
    NotificationChannelModel,
)

_DEFAULT_WORKSPACE = uuid.UUID("00000000-0000-0000-0000-000000000000")


class AlertService:
    """Service for alert rule, event, silence, policy, and channel management.

    Args:
        db: Async SQLAlchemy session.
        workspace_id: Optional workspace scope. Defaults to zero-UUID.
    """

    def __init__(self, db: AsyncSession, workspace_id: uuid.UUID | None = None) -> None:
        self._db = db
        self._workspace_id = workspace_id or _DEFAULT_WORKSPACE

    async def create_rule(self, **kwargs: object) -> AlertRuleModel:
        """Create a new alert rule."""
        rule = AlertRuleModel(workspace_id=self._workspace_id, **kwargs)
        self._db.add(rule)
        await self._db.flush()
        return rule

    async def list_rules(self, *, enabled_only: bool = False) -> list[AlertRuleModel]:
        """List alert rules for the current workspace."""
        stmt = select(AlertRuleModel).where(
            AlertRuleModel.workspace_id == self._workspace_id,
            AlertRuleModel.deleted == False,  # noqa: E712
        )
        if enabled_only:
            stmt = stmt.where(AlertRuleModel.enabled == True)  # noqa: E712
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def get_rule(self, rule_id: uuid.UUID) -> AlertRuleModel | None:
        """Get a single alert rule by ID."""
        stmt = select(AlertRuleModel).where(
            AlertRuleModel.id == rule_id,
            AlertRuleModel.deleted == False,  # noqa: E712
        )
        return (await self._db.execute(stmt)).scalar_one_or_none()

    async def update_rule(self, rule_id: uuid.UUID, **kwargs: object) -> AlertRuleModel | None:
        """Update an alert rule."""
        rule = await self.get_rule(rule_id)
        if rule is None:
            return None
        for key, value in kwargs.items():
            if value is not None and hasattr(rule, key):
                setattr(rule, key, value)
        await self._db.flush()
        return rule

    async def delete_rule(self, rule_id: uuid.UUID) -> bool:
        """Soft-delete an alert rule."""
        rule = await self.get_rule(rule_id)
        if rule is None:
            return False
        rule.deleted = True
        rule.deleted_at = datetime.now(UTC)
        await self._db.flush()
        return True

    async def create_event(self, **kwargs: object) -> AlertEventModel:
        """Create a new alert event."""
        event = AlertEventModel(workspace_id=self._workspace_id, **kwargs)
        self._db.add(event)
        await self._db.flush()
        return event

    async def list_events(
        self,
        *,
        state: str | None = None,
        rule_id: uuid.UUID | None = None,
    ) -> list[AlertEventModel]:
        """List alert events with optional filters."""
        stmt = select(AlertEventModel).where(
            AlertEventModel.workspace_id == self._workspace_id,
            AlertEventModel.deleted == False,  # noqa: E712
        )
        if state:
            stmt = stmt.where(AlertEventModel.state == state)
        if rule_id:
            stmt = stmt.where(AlertEventModel.rule_id == rule_id)
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def get_event(self, event_id: uuid.UUID) -> AlertEventModel | None:
        """Get a single alert event by ID."""
        stmt = select(AlertEventModel).where(AlertEventModel.id == event_id)
        return (await self._db.execute(stmt)).scalar_one_or_none()

    async def get_active_event_for_rule(self, rule_id: uuid.UUID) -> AlertEventModel | None:
        """Get the active (pending or firing) event for a rule."""
        stmt = select(AlertEventModel).where(
            AlertEventModel.rule_id == rule_id,
            AlertEventModel.state.in_([AlertState.PENDING, AlertState.FIRING]),
        )
        return (await self._db.execute(stmt)).scalar_one_or_none()

    async def acknowledge_event(self, event_id: uuid.UUID, user_id: uuid.UUID) -> AlertEventModel | None:
        """Acknowledge an alert event."""
        event = await self.get_event(event_id)
        if event is None:
            return None
        event.state = AlertState.ACKED
        event.acked_at = datetime.now(UTC)
        event.acked_by = user_id
        await self._db.flush()
        return event

    async def create_silence(self, **kwargs: object) -> AlertSilenceModel:
        """Create a new silence window."""
        silence = AlertSilenceModel(workspace_id=self._workspace_id, **kwargs)
        self._db.add(silence)
        await self._db.flush()
        return silence

    async def list_silences(self, *, active_only: bool = False) -> list[AlertSilenceModel]:
        """List silence windows."""
        stmt = select(AlertSilenceModel).where(
            AlertSilenceModel.workspace_id == self._workspace_id,
            AlertSilenceModel.deleted == False,  # noqa: E712
        )
        if active_only:
            now = datetime.now(UTC)
            stmt = stmt.where(AlertSilenceModel.start_at <= now, AlertSilenceModel.end_at >= now)
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def delete_silence(self, silence_id: uuid.UUID) -> bool:
        """Delete a silence window."""
        stmt = select(AlertSilenceModel).where(AlertSilenceModel.id == silence_id)
        silence = (await self._db.execute(stmt)).scalar_one_or_none()
        if silence is None:
            return False
        silence.deleted = True
        silence.deleted_at = datetime.now(UTC)
        await self._db.flush()
        return True

    async def is_silenced(
        self,
        rule_id: uuid.UUID,
        severity: str,
    ) -> bool:
        """Check if a rule+severity combination is currently silenced."""
        now = datetime.now(UTC)
        stmt = select(AlertSilenceModel).where(
            AlertSilenceModel.workspace_id == self._workspace_id,
            AlertSilenceModel.start_at <= now,
            AlertSilenceModel.end_at >= now,
            AlertSilenceModel.deleted == False,  # noqa: E712
        )
        silences = (await self._db.execute(stmt)).scalars().all()
        for silence in silences:
            matchers = silence.matchers or {}
            rule_ids = matchers.get("rule_ids")
            if rule_ids and str(rule_id) not in rule_ids:
                continue
            severities = matchers.get("severity")
            if severities and severity not in severities:
                continue
            return True
        return False

    async def create_policy(self, **kwargs: object) -> EscalationPolicyModel:
        """Create an escalation policy."""
        policy = EscalationPolicyModel(workspace_id=self._workspace_id, **kwargs)
        self._db.add(policy)
        await self._db.flush()
        return policy

    async def list_policies(self) -> list[EscalationPolicyModel]:
        """List escalation policies."""
        stmt = select(EscalationPolicyModel).where(
            EscalationPolicyModel.workspace_id == self._workspace_id,
            EscalationPolicyModel.deleted == False,  # noqa: E712
        )
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def get_policy(self, policy_id: uuid.UUID) -> EscalationPolicyModel | None:
        """Get a single escalation policy."""
        stmt = select(EscalationPolicyModel).where(EscalationPolicyModel.id == policy_id)
        return (await self._db.execute(stmt)).scalar_one_or_none()

    async def update_policy(self, policy_id: uuid.UUID, **kwargs: object) -> EscalationPolicyModel | None:
        """Update an escalation policy."""
        policy = await self.get_policy(policy_id)
        if policy is None:
            return None
        for key, value in kwargs.items():
            if value is not None and hasattr(policy, key):
                setattr(policy, key, value)
        await self._db.flush()
        return policy

    async def delete_policy(self, policy_id: uuid.UUID) -> bool:
        """Soft-delete an escalation policy."""
        policy = await self.get_policy(policy_id)
        if policy is None:
            return False
        policy.deleted = True
        policy.deleted_at = datetime.now(UTC)
        await self._db.flush()
        return True

    async def create_channel(self, **kwargs: object) -> NotificationChannelModel:
        """Create a notification channel."""
        channel = NotificationChannelModel(workspace_id=self._workspace_id, **kwargs)
        self._db.add(channel)
        await self._db.flush()
        return channel

    async def list_channels(self) -> list[NotificationChannelModel]:
        """List notification channels."""
        stmt = select(NotificationChannelModel).where(
            NotificationChannelModel.workspace_id == self._workspace_id,
            NotificationChannelModel.deleted == False,  # noqa: E712
        )
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def get_channel(self, channel_id: uuid.UUID) -> NotificationChannelModel | None:
        """Get a single notification channel."""
        stmt = select(NotificationChannelModel).where(NotificationChannelModel.id == channel_id)
        return (await self._db.execute(stmt)).scalar_one_or_none()

    async def update_channel(self, channel_id: uuid.UUID, **kwargs: object) -> NotificationChannelModel | None:
        """Update a notification channel."""
        channel = await self.get_channel(channel_id)
        if channel is None:
            return None
        for key, value in kwargs.items():
            if value is not None and hasattr(channel, key):
                setattr(channel, key, value)
        await self._db.flush()
        return channel

    async def delete_channel(self, channel_id: uuid.UUID) -> bool:
        """Soft-delete a notification channel."""
        channel = await self.get_channel(channel_id)
        if channel is None:
            return False
        channel.deleted = True
        channel.deleted_at = datetime.now(UTC)
        await self._db.flush()
        return True

    async def get_channels_by_ids(self, channel_ids: list[uuid.UUID]) -> list[NotificationChannelModel]:
        """Get multiple notification channels by their IDs."""
        if not channel_ids:
            return []
        stmt = select(NotificationChannelModel).where(
            NotificationChannelModel.id.in_(channel_ids),
            NotificationChannelModel.enabled == True,  # noqa: E712
            NotificationChannelModel.deleted == False,  # noqa: E712
        )
        result = await self._db.execute(stmt)
        return list(result.scalars().all())
