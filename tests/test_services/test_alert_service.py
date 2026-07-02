"""Tests for the alerting system: models, service CRUD, dispatcher templates."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from hecate.models.alert import (
    AlertEventModel,
    AlertRuleModel,
    AlertSeverity,
    AlertSilenceModel,
    AlertState,
    AlertType,
    ChannelType,
    EscalationPolicyModel,
    NotificationChannelModel,
)
from hecate.services.alert_service import AlertService
from hecate.services.notification_dispatcher import (
    NotificationDispatcher,
    render_dingtalk_markdown,
    render_feishu_card,
    render_generic_webhook,
    render_slack_blocks,
    render_wecom_markdown,
)


class TestAlertModels:
    """Test ORM model creation and schema validation."""

    async def test_create_alert_rule(self, db_session):
        rule = AlertRuleModel(
            name="High Error Rate",
            alert_type=AlertType.ERROR_RATE,
            threshold=0.05,
            window_minutes=5,
            for_minutes=2,
            severity=AlertSeverity.CRITICAL,
        )
        db_session.add(rule)
        await db_session.flush()
        assert rule.id is not None
        assert rule.enabled is True
        assert rule.severity == "critical"

    async def test_create_alert_event(self, db_session):
        rule = AlertRuleModel(
            name="Test",
            alert_type=AlertType.ERROR_RATE,
            threshold=0.1,
        )
        db_session.add(rule)
        await db_session.flush()

        event = AlertEventModel(
            rule_id=rule.id,
            state=AlertState.PENDING,
            current_value=0.15,
        )
        db_session.add(event)
        await db_session.flush()
        assert event.id is not None
        assert event.state == "pending"
        assert event.escalation_step == 0

    async def test_create_silence(self, db_session):
        now = datetime.now(UTC)
        silence = AlertSilenceModel(
            start_at=now,
            end_at=now + timedelta(hours=4),
            matchers={"rule_ids": []},
            reason="Maintenance window",
        )
        db_session.add(silence)
        await db_session.flush()
        assert silence.id is not None

    async def test_create_escalation_policy(self, db_session):
        policy = EscalationPolicyModel(
            name="Standard",
            steps=[{"delay_min": 0, "channel_types": ["webhook_feishu"]}],
            repeat_interval_min=60,
        )
        db_session.add(policy)
        await db_session.flush()
        assert policy.id is not None
        assert len(policy.steps) == 1

    async def test_create_notification_channel(self, db_session):
        channel = NotificationChannelModel(
            name="Feishu Bot",
            channel_type=ChannelType.WEBHOOK_FEISHU,
            config={"url": "https://open.feishu.cn/xxx"},
        )
        db_session.add(channel)
        await db_session.flush()
        assert channel.id is not None
        assert channel.enabled is True


class TestAlertServiceCRUD:
    """Test AlertService CRUD operations."""

    async def test_rule_crud(self, db_session):
        service = AlertService(db_session)
        rule = await service.create_rule(
            name="Latency Alert",
            alert_type=AlertType.LATENCY_P95,
            threshold=5000.0,
            window_minutes=10,
        )
        assert rule.id is not None

        rules = await service.list_rules()
        assert len(rules) == 1

        fetched = await service.get_rule(rule.id)
        assert fetched is not None
        assert fetched.name == "Latency Alert"

        updated = await service.update_rule(rule.id, threshold=3000.0)
        assert updated.threshold == 3000.0

        deleted = await service.delete_rule(rule.id)
        assert deleted is True

    async def test_event_ack(self, db_session):
        service = AlertService(db_session)
        rule = await service.create_rule(
            name="Test",
            alert_type=AlertType.ERROR_RATE,
            threshold=0.05,
        )
        event = await service.create_event(
            rule_id=rule.id,
            state=AlertState.FIRING,
            current_value=0.1,
        )
        user_id = uuid.uuid4()
        acked = await service.acknowledge_event(event.id, user_id)
        assert acked.state == "acked"
        assert acked.acked_by == user_id
        assert acked.acked_at is not None

    async def test_silence_crud(self, db_session):
        service = AlertService(db_session)
        now = datetime.now(UTC)
        silence = await service.create_silence(
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(hours=1),
            reason="Deploy",
        )
        active = await service.list_silences(active_only=True)
        assert len(active) == 1

        await service.create_silence(
            start_at=now - timedelta(hours=3),
            end_at=now - timedelta(hours=2),
            reason="Old",
        )
        all_silences = await service.list_silences()
        assert len(all_silences) == 2
        active_only = await service.list_silences(active_only=True)
        assert len(active_only) == 1

        await service.delete_silence(silence.id)

    async def test_is_silenced(self, db_session):
        service = AlertService(db_session)
        now = datetime.now(UTC)
        rule_id = uuid.uuid4()

        await service.create_silence(
            start_at=now - timedelta(minutes=5),
            end_at=now + timedelta(minutes=5),
            matchers={"rule_ids": [str(rule_id)]},
        )
        assert await service.is_silenced(rule_id, "critical") is True
        assert await service.is_silenced(uuid.uuid4(), "critical") is False

    async def test_channel_crud(self, db_session):
        service = AlertService(db_session)
        channel = await service.create_channel(
            name="Slack",
            channel_type=ChannelType.WEBHOOK_SLACK,
            config={"url": "https://hooks.slack.com/xxx"},
        )
        channels = await service.list_channels()
        assert len(channels) == 1

        fetched = await service.get_channel(channel.id)
        assert fetched.channel_type == "webhook_slack"

    async def test_policy_crud(self, db_session):
        service = AlertService(db_session)
        policy = await service.create_policy(
            name="Aggressive",
            steps=[{"delay_min": 0, "channel_types": ["websocket"]}],
            repeat_interval_min=30,
        )
        policies = await service.list_policies()
        assert len(policies) == 1

        fetched = await service.get_policy(policy.id)
        assert fetched.name == "Aggressive"


class TestNotificationTemplates:
    """Test message template rendering."""

    def _make_rule(self) -> AlertRuleModel:
        return AlertRuleModel(
            name="High Error Rate",
            alert_type=AlertType.ERROR_RATE,
            threshold=0.05,
            severity=AlertSeverity.CRITICAL,
        )

    def _make_event(self, rule: AlertRuleModel) -> AlertEventModel:
        return AlertEventModel(
            rule_id=rule.id,
            state=AlertState.FIRING,
            current_value=0.12,
            fired_at=datetime.now(UTC),
        )

    def test_feishu_card(self):
        rule = self._make_rule()
        event = self._make_event(rule)
        card = render_feishu_card(event, rule)
        assert card["msg_type"] == "interactive"
        assert "card" in card
        header = card["card"]["header"]["title"]["content"]
        assert "CRITICAL" in header

    def test_wecom_markdown(self):
        rule = self._make_rule()
        event = self._make_event(rule)
        payload = render_wecom_markdown(event, rule)
        assert payload["msgtype"] == "markdown"
        assert "0.12" in payload["markdown"]["content"]

    def test_dingtalk_markdown(self):
        rule = self._make_rule()
        event = self._make_event(rule)
        payload = render_dingtalk_markdown(event, rule)
        assert payload["msgtype"] == "markdown"
        assert "CRITICAL" in payload["markdown"]["title"]

    def test_slack_blocks(self):
        rule = self._make_rule()
        event = self._make_event(rule)
        payload = render_slack_blocks(event, rule)
        assert "attachments" in payload
        blocks = payload["attachments"][0]["blocks"]
        assert any(b["type"] == "header" for b in blocks)

    def test_generic_webhook(self):
        rule = self._make_rule()
        event = self._make_event(rule)
        payload = render_generic_webhook(event, rule)
        assert payload["severity"] == "critical"
        assert payload["current_value"] == 0.12
        assert payload["threshold"] == 0.05

    def test_dispatcher_skips_disabled_channels(self):
        rule = self._make_rule()
        event = self._make_event(rule)
        channel = NotificationChannelModel(
            name="Disabled",
            channel_type=ChannelType.WEBSOCKET,
            enabled=False,
        )
        dispatcher = NotificationDispatcher()
        import asyncio

        results = asyncio.get_event_loop().run_until_complete(dispatcher.dispatch(event, rule, [channel]))
        assert len(results) == 0
