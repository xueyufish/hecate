"""Alert evaluator background task.

Runs an asyncio loop that acquires a PostgreSQL advisory lock, loads enabled
rules, evaluates each rule's signal against its threshold, manages event state
transitions (pending → firing → resolved), and dispatches notifications through
escalation policies.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.config import settings
from hecate.core.database import async_session_factory
from hecate.models.alert import AlertEventModel, AlertRuleModel, AlertState, AlertType
from hecate.services.alert_service import AlertService
from hecate.services.notification_dispatcher import NotificationDispatcher
from hecate.services.signal_provider import SignalProviderRegistry

logger = logging.getLogger(__name__)

_ALERT_LOCK_ID = 0x416C7274


class AlertEvaluator:
    """Periodic alert evaluation engine.

    Args:
        dispatcher: Notification dispatcher instance.
        registry: Signal provider registry.
    """

    def __init__(
        self,
        dispatcher: NotificationDispatcher | None = None,
        registry: SignalProviderRegistry | None = None,
    ) -> None:
        self._dispatcher = dispatcher or NotificationDispatcher()
        self._registry = registry or SignalProviderRegistry()
        self._task: asyncio.Task[None] | None = None

    @property
    def is_running(self) -> bool:
        """Return whether the evaluation loop is active."""
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        """Start the background evaluation loop."""
        if self.is_running:
            return
        self._task = asyncio.create_task(self._eval_loop())
        logger.info("AlertEvaluator started (interval: %ds)", settings.ALERT_EVAL_INTERVAL)

    async def stop(self) -> None:
        """Stop the evaluation loop."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info("AlertEvaluator stopped")

    async def _eval_loop(self) -> None:
        """Background loop that evaluates alert rules periodically."""
        while True:
            try:
                await self._evaluate_cycle()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Error in alert evaluation cycle")
            await asyncio.sleep(settings.ALERT_EVAL_INTERVAL)

    async def _evaluate_cycle(self) -> None:
        """Run a single evaluation cycle."""
        async with async_session_factory() as db:
            locked = await self._try_advisory_lock(db)
            if not locked:
                return
            try:
                await self._evaluate_rules(db)
                await db.commit()
            except Exception:
                await db.rollback()
                raise
            finally:
                await self._release_advisory_lock(db)

    async def _try_advisory_lock(self, db: AsyncSession) -> bool:
        """Try to acquire a session-level advisory lock."""
        result = await db.execute(
            text("SELECT pg_try_advisory_lock(:lock_id)"),
            {"lock_id": _ALERT_LOCK_ID},
        )
        return bool(result.scalar())

    async def _release_advisory_lock(self, db: AsyncSession) -> None:
        """Release the advisory lock."""
        await db.execute(
            text("SELECT pg_advisory_unlock(:lock_id)"),
            {"lock_id": _ALERT_LOCK_ID},
        )

    async def _evaluate_rules(self, db: AsyncSession) -> None:
        """Evaluate all enabled rules and manage state transitions."""
        service = AlertService(db)
        rules = await service.list_rules(enabled_only=True)

        for rule in rules:
            try:
                await self._evaluate_single_rule(db, service, rule)
            except Exception:
                logger.exception("Error evaluating rule %s (%s)", rule.id, rule.name)

    async def _evaluate_single_rule(
        self,
        db: AsyncSession,
        service: AlertService,
        rule: AlertRuleModel,
    ) -> None:
        """Evaluate a single rule and manage its event lifecycle."""
        alert_type = AlertType(rule.alert_type)
        provider = self._registry.get_provider(alert_type)
        if provider is None:
            logger.warning("No signal provider for alert type %s", rule.alert_type)
            return

        current_value = await provider.get_value(db, rule)
        condition_met = (
            current_value < rule.threshold
            if rule.alert_type == AlertType.SUCCESS_RATE.value
            else current_value > rule.threshold
        )

        existing = await service.get_active_event_for_rule(rule.id)
        now = datetime.now(UTC)

        if not condition_met:
            if existing and existing.state in (AlertState.PENDING, AlertState.FIRING):
                existing.state = AlertState.RESOLVED
                existing.resolved_at = now
                await db.flush()
            return

        if existing is None:
            await service.create_event(
                rule_id=rule.id,
                state=AlertState.PENDING,
                current_value=current_value,
            )
            return

        existing.current_value = current_value

        if existing.state == AlertState.PENDING:
            threshold_time = existing.created_at.replace(tzinfo=UTC) + timedelta(minutes=rule.for_minutes)
            if now >= threshold_time:
                existing.state = AlertState.FIRING
                existing.fired_at = now
                await self._handle_firing(db, service, rule, existing)

        elif existing.state == AlertState.FIRING:
            await self._handle_firing(db, service, rule, existing)

    async def _handle_firing(
        self,
        db: AsyncSession,
        service: AlertService,
        rule: AlertRuleModel,
        event: AlertEventModel,
    ) -> None:
        """Handle escalation and notification for a firing event."""
        if await service.is_silenced(rule.id, rule.severity):
            return

        channels = []
        if rule.channel_ids:
            channel_uuids = [cid if isinstance(cid, uuid.UUID) else uuid.UUID(str(cid)) for cid in rule.channel_ids]
            channels = await service.get_channels_by_ids(channel_uuids)

        if not channels:
            return

        if rule.escalation_policy_id:
            policy = await service.get_policy(rule.escalation_policy_id)
            if policy:
                current_step = self._compute_escalation_step(event, policy.steps)
                if current_step > event.escalation_step:
                    event.escalation_step = current_step
                step_channels = self._filter_channels_for_step(channels, policy.steps, current_step)
                if step_channels:
                    await self._dispatcher.dispatch(event, rule, step_channels)

                if policy.repeat_interval_min and event.fired_at:
                    last_dispatch = event.fired_at + timedelta(
                        minutes=policy.repeat_interval_min * (1 + event.escalation_step)
                    )
                    if datetime.now(UTC) >= last_dispatch and event.escalation_step == current_step:
                        await self._dispatcher.dispatch(event, rule, channels)
            else:
                await self._dispatcher.dispatch(event, rule, channels)
        else:
            await self._dispatcher.dispatch(event, rule, channels)

    def _compute_escalation_step(
        self,
        event: AlertEventModel,
        steps: list[dict],
    ) -> int:
        """Compute the current escalation step based on elapsed time since firing."""
        if not event.fired_at or not steps:
            return 0
        elapsed = (datetime.now(UTC) - event.fired_at.replace(tzinfo=UTC)).total_seconds() / 60
        current = 0
        for i, step in enumerate(steps):
            if elapsed >= step.get("delay_min", 0):
                current = i
        return current

    def _filter_channels_for_step(
        self,
        all_channels: list,
        steps: list[dict],
        step_index: int,
    ) -> list:
        """Filter channels to only those in the current escalation step."""
        if step_index >= len(steps):
            return all_channels
        step_channel_types = steps[step_index].get("channel_types", [])
        if not step_channel_types:
            return all_channels
        return [ch for ch in all_channels if ch.channel_type in step_channel_types]
