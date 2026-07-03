"""Notification dispatcher with built-in IM message templates.

Formats alert events into platform-native payloads (Feishu card, WeCom markdown,
DingTalk markdown, Slack Block Kit, generic JSON, email HTML) and dispatches
via HTTP webhook, aiosmtplib email, or WebSocket broadcast.
"""

from __future__ import annotations

import logging
from typing import Any

from hecate.channel.notification import (
    EmailNotificationAdapter,
    NotificationChannelAdapter,
    WebhookNotificationAdapter,
    WebSocketNotificationAdapter,
)
from hecate.core.config import settings
from hecate.models.alert import AlertEventModel, AlertRuleModel, ChannelType, NotificationChannelModel

logger = logging.getLogger(__name__)

_SEVERITY_ICONS = {"critical": "🔴", "warning": "🟡", "info": "🔵"}


def _format_value(value: float) -> str:
    """Format a float value for display."""
    if value < 1:
        return f"{value:.4f}"
    return f"{value:.2f}"


def render_feishu_card(event: AlertEventModel, rule: AlertRuleModel) -> dict[str, Any]:
    """Render alert event as Feishu interactive card."""
    icon = _SEVERITY_ICONS.get(rule.severity, "⚪")
    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": f"{icon} [{rule.severity.upper()}] {rule.name}"},
                "template": "red" if rule.severity == "critical" else "orange",
            },
            "elements": [
                {
                    "tag": "div",
                    "fields": [
                        {
                            "is_short": True,
                            "text": {"tag": "lark_md", "content": f"**Current**\n{_format_value(event.current_value)}"},
                        },
                        {"tag": "lark_md", "content": f"**Threshold**\n{_format_value(rule.threshold)}"},
                    ],
                },
                {
                    "tag": "note",
                    "elements": [
                        {"tag": "plain_text", "content": f"Type: {rule.alert_type} | Fired: {event.fired_at}"},
                    ],
                },
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "Acknowledge"},
                            "type": "primary",
                            "value": {"action": "ack", "event_id": str(event.id)},
                        },
                    ],
                },
            ],
        },
    }


def render_wecom_markdown(event: AlertEventModel, rule: AlertRuleModel) -> dict[str, Any]:
    """Render alert event as WeCom markdown."""
    icon = _SEVERITY_ICONS.get(rule.severity, "⚪")
    content = (
        f"{icon} **[{rule.severity.upper()}] {rule.name}**\n"
        f"> Current: **{_format_value(event.current_value)}**\n"
        f"> Threshold: **{_format_value(rule.threshold)}**\n"
        f"> Type: {rule.alert_type}\n"
        f"> Fired: {event.fired_at}"
    )
    return {"msgtype": "markdown", "markdown": {"content": content}}


def render_dingtalk_markdown(event: AlertEventModel, rule: AlertRuleModel) -> dict[str, Any]:
    """Render alert event as DingTalk markdown."""
    icon = _SEVERITY_ICONS.get(rule.severity, "⚪")
    content = (
        f"### {icon} [{rule.severity.upper()}] {rule.name}\n\n"
        f"- Current: **{_format_value(event.current_value)}**\n"
        f"- Threshold: **{_format_value(rule.threshold)}**\n"
        f"- Type: {rule.alert_type}\n"
        f"- Fired: {event.fired_at}\n"
    )
    return {"msgtype": "markdown", "markdown": {"title": f"{rule.severity.upper()} Alert", "text": content}}


def render_slack_blocks(event: AlertEventModel, rule: AlertRuleModel) -> dict[str, Any]:
    """Render alert event as Slack Block Kit."""
    icon = _SEVERITY_ICONS.get(rule.severity, "⚪")
    color = "#FF0000" if rule.severity == "critical" else "#FFA500"
    return {
        "attachments": [
            {
                "color": color,
                "blocks": [
                    {
                        "type": "header",
                        "text": {"type": "plain_text", "text": f"{icon} [{rule.severity.upper()}] {rule.name}"},
                    },
                    {
                        "type": "section",
                        "fields": [
                            {"type": "mrkdwn", "text": f"*Current:*\n{_format_value(event.current_value)}"},
                            {"type": "mrkdwn", "text": f"*Threshold:*\n{_format_value(rule.threshold)}"},
                        ],
                    },
                    {
                        "type": "context",
                        "elements": [{"type": "mrkdwn", "text": f"Type: {rule.alert_type} | Fired: {event.fired_at}"}],
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "Acknowledge"},
                                "style": "primary",
                                "value": str(event.id),
                            },
                        ],
                    },
                ],
            }
        ],
    }


def render_generic_webhook(event: AlertEventModel, rule: AlertRuleModel) -> dict[str, Any]:
    """Render alert event as generic JSON webhook payload."""
    return {
        "event_id": str(event.id),
        "rule_id": str(rule.id),
        "rule_name": rule.name,
        "alert_type": rule.alert_type,
        "severity": rule.severity,
        "current_value": event.current_value,
        "threshold": rule.threshold,
        "state": event.state,
        "fired_at": event.fired_at.isoformat() if event.fired_at else None,
    }


def render_email(event: AlertEventModel, rule: AlertRuleModel) -> tuple[str, str]:
    """Render alert event as HTML email. Returns (subject, html_body)."""
    icon = _SEVERITY_ICONS.get(rule.severity, "⚪")
    subject = f"[Hecate Alert] {rule.severity.upper()} - {rule.name}"
    cur = _format_value(event.current_value)
    thr = _format_value(rule.threshold)
    html = f"""
    <html><body>
    <h2>{icon} [{rule.severity.upper()}] {rule.name}</h2>
    <table style="border-collapse:collapse;">
        <tr><td style="padding:4px 16px 4px 0;font-weight:bold;">Current Value:</td><td>{cur}</td></tr>
        <tr><td style="padding:4px 16px 4px 0;font-weight:bold;">Threshold:</td><td>{thr}</td></tr>
        <tr><td style="padding:4px 16px 4px 0;font-weight:bold;">Alert Type:</td><td>{rule.alert_type}</td></tr>
        <tr><td style="padding:4px 16px 4px 0;font-weight:bold;">Fired At:</td><td>{event.fired_at}</td></tr>
        <tr><td style="padding:4px 16px 4px 0;font-weight:bold;">Event ID:</td><td>{event.id}</td></tr>
    </table>
    </body></html>
    """
    return subject, html


# Map ChannelType to render functions
_RENDER_MAP: dict[str, Any] = {
    ChannelType.WEBHOOK_FEISHU: render_feishu_card,
    ChannelType.WEBHOOK_WECOM: render_wecom_markdown,
    ChannelType.WEBHOOK_DINGTALK: render_dingtalk_markdown,
    ChannelType.WEBHOOK_SLACK: render_slack_blocks,
    ChannelType.WEBHOOK_GENERIC: render_generic_webhook,
}


def _build_adapter_map(connection_manager: Any = None) -> dict[str, NotificationChannelAdapter]:
    """Build a map of channel type to NotificationChannelAdapter."""
    adapters: dict[str, NotificationChannelAdapter] = {}

    # Webhook adapters
    for ct, render_fn in _RENDER_MAP.items():
        adapters[ct] = WebhookNotificationAdapter(
            channel_name=ct,
            channel_description=f"{ct} webhook notification channel",
            render_fn=render_fn,
        )

    # WebSocket adapter
    adapters[ChannelType.WEBSOCKET] = WebSocketNotificationAdapter(connection_manager)

    # Email adapter
    adapters[ChannelType.EMAIL] = EmailNotificationAdapter()

    return adapters


class NotificationDispatcher:
    """Dispatches alert notifications to configured channels.

    Uses NotificationChannelAdapter implementations instead of switch/case
    dispatch. Each channel type has a corresponding adapter that handles
    the platform-specific payload rendering and delivery.

    Args:
        connection_manager: Optional ConnectionManager for WebSocket dispatch.
    """

    def __init__(self, connection_manager: Any = None) -> None:
        self._connection_manager = connection_manager
        self._adapters = _build_adapter_map(connection_manager)

    async def dispatch(
        self,
        event: AlertEventModel,
        rule: AlertRuleModel,
        channels: list[NotificationChannelModel],
    ) -> list[dict[str, Any]]:
        """Dispatch an alert event to a list of channels.

        Returns a list of per-channel results: {channel_id, success, error}.
        """
        results: list[dict[str, Any]] = []
        for channel in channels:
            if not channel.enabled:
                continue
            try:
                await self._dispatch_single(event, rule, channel)
                results.append({"channel_id": str(channel.id), "success": True})
            except Exception as e:
                logger.warning("Dispatch to channel %s failed: %s", channel.id, e)
                results.append({"channel_id": str(channel.id), "success": False, "error": str(e)})
        return results

    async def _dispatch_single(
        self,
        event: AlertEventModel,
        rule: AlertRuleModel,
        channel: NotificationChannelModel,
    ) -> None:
        """Dispatch to a single channel using its adapter."""
        ct = channel.channel_type
        adapter = self._adapters.get(ct)
        if adapter is None:
            logger.warning("Unknown channel type: %s", ct)
            return

        # Build the adapter-specific response payload
        if ct == ChannelType.WEBSOCKET:
            response: dict[str, Any] = {
                "message": {
                    "type": "alert_firing",
                    "event_id": str(event.id),
                    "rule_name": rule.name,
                    "severity": rule.severity,
                    "alert_type": rule.alert_type,
                    "current_value": event.current_value,
                    "threshold": rule.threshold,
                    "fired_at": event.fired_at.isoformat() if event.fired_at else None,
                }
            }
        elif ct == ChannelType.EMAIL:
            subject, html_body = render_email(event, rule)
            response = {
                "subject": subject,
                "html_body": html_body,
                "recipients": channel.config.get("recipients", []),
                "config": {
                    "smtp_from": settings.ALERT_SMTP_FROM,
                    "smtp_host": settings.ALERT_SMTP_HOST,
                    "smtp_port": settings.ALERT_SMTP_PORT,
                    "smtp_user": settings.ALERT_SMTP_USER,
                    "smtp_password": settings.ALERT_SMTP_PASSWORD,
                },
            }
        else:
            # Webhook channels
            response = {
                "event": event,
                "rule": rule,
                "url": channel.config.get("url", ""),
            }

        await adapter.respond(str(channel.id), response)
