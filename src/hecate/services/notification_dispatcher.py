"""Notification dispatcher with built-in IM message templates.

Formats alert events into platform-native payloads (Feishu card, WeCom markdown,
DingTalk markdown, Slack Block Kit, generic JSON, email HTML) and dispatches
via HTTP webhook, aiosmtplib email, or WebSocket broadcast.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

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


class NotificationDispatcher:
    """Dispatches alert notifications to configured channels.

    Args:
        connection_manager: Optional ConnectionManager for WebSocket dispatch.
    """

    def __init__(self, connection_manager: Any = None) -> None:
        self._connection_manager = connection_manager

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
        """Dispatch to a single channel based on its type."""
        ct = channel.channel_type

        if ct == ChannelType.WEBSOCKET:
            await self._dispatch_websocket(event, rule)
        elif ct == ChannelType.EMAIL:
            await self._dispatch_email(event, rule, channel)
        elif ct.startswith("webhook_"):
            await self._dispatch_webhook(event, rule, channel)
        else:
            logger.warning("Unknown channel type: %s", ct)

    async def _dispatch_webhook(
        self,
        event: AlertEventModel,
        rule: AlertRuleModel,
        channel: NotificationChannelModel,
    ) -> None:
        """Send webhook with platform-specific payload and retry logic."""
        ct = channel.channel_type
        url = channel.config.get("url", "")
        if not url:
            logger.warning("Webhook channel %s has no URL configured", channel.id)
            return

        if ct == ChannelType.WEBHOOK_FEISHU:
            payload: dict[str, Any] = render_feishu_card(event, rule)
        elif ct == ChannelType.WEBHOOK_WECOM:
            payload = render_wecom_markdown(event, rule)
        elif ct == ChannelType.WEBHOOK_DINGTALK:
            payload = render_dingtalk_markdown(event, rule)
        elif ct == ChannelType.WEBHOOK_SLACK:
            payload = render_slack_blocks(event, rule)
        else:
            payload = render_generic_webhook(event, rule)

        backoff_times = [1, 2, 4]
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(url, json=payload)
                    if resp.status_code < 500:
                        return
                    logger.warning("Webhook %s returned %d (attempt %d)", url, resp.status_code, attempt + 1)
            except (httpx.TimeoutException, httpx.HTTPError) as e:
                logger.warning("Webhook %s failed (attempt %d): %s", url, attempt + 1, e)
            if attempt < 2:
                await asyncio.sleep(backoff_times[attempt])
        logger.error("Webhook %s failed after 3 retries", url)

    async def _dispatch_websocket(self, event: AlertEventModel, rule: AlertRuleModel) -> None:
        """Broadcast alert via WebSocket to connected clients."""
        if self._connection_manager is None:
            return
        message = {
            "type": "alert_firing",
            "event_id": str(event.id),
            "rule_name": rule.name,
            "severity": rule.severity,
            "alert_type": rule.alert_type,
            "current_value": event.current_value,
            "threshold": rule.threshold,
            "fired_at": event.fired_at.isoformat() if event.fired_at else None,
        }
        await self._connection_manager.broadcast(message)

    async def _dispatch_email(
        self,
        event: AlertEventModel,
        rule: AlertRuleModel,
        channel: NotificationChannelModel,
    ) -> None:
        """Send email via SMTP."""
        recipients = channel.config.get("recipients", [])
        if not recipients:
            return

        subject, html_body = render_email(event, rule)

        try:
            from email.mime.multipart import MIMEMultipart
            from email.mime.text import MIMEText

            import aiosmtplib

            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = settings.ALERT_SMTP_FROM
            msg["To"] = ", ".join(recipients)
            msg.attach(MIMEText(html_body, "html"))

            await aiosmtplib.send(
                msg,
                hostname=settings.ALERT_SMTP_HOST,
                port=settings.ALERT_SMTP_PORT,
                username=settings.ALERT_SMTP_USER or None,
                password=settings.ALERT_SMTP_PASSWORD or None,
                recipients=recipients,
            )
        except ImportError:
            logger.warning("aiosmtplib not installed — email alert skipped")
        except Exception as e:
            logger.warning("Email send failed: %s", e)
