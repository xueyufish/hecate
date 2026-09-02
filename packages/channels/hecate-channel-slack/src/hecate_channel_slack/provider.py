"""Slack channel entry-point factory.

Registered under the ``hecate.channel_providers`` group as ``slack``. Core
discovers this entry point via ``importlib.metadata``; when
``HECATE_CHANNEL_PROVIDERS`` lists ``slack`` (it does by default), the
resolver loads this factory and registers the returned adapter with the
plugin registry.

Third-party channel packages (e.g. a future ``hecate-channel-dingtalk``)
declare their own entry under the same group with a distinct name and
subclass ``ChannelBase`` from ``hecate.channel.adapter`` — the same
contract, no registration code in core.
"""

from __future__ import annotations

import os

from .channel import SlackChannel, create_slack_channel


def provider() -> SlackChannel | None:
    """Zero-arg entry-point factory for ``hecate.channel_providers``.

    Reads its own ``HECATE_IM_SLACK_*`` env configuration and returns a
    configured :class:`SlackChannel`, or ``None`` when unconfigured — the
    resolver skips ``None`` without blocking boot.
    """
    bot_token = os.environ.get("HECATE_IM_SLACK_BOT_TOKEN")
    signing_secret = os.environ.get("HECATE_IM_SLACK_SIGNING_SECRET")
    if not bot_token or not signing_secret:
        return None
    return create_slack_channel(
        bot_token=bot_token,
        signing_secret=signing_secret,
        app_token=os.environ.get("HECATE_IM_SLACK_APP_TOKEN"),
        token_verification_enabled=os.environ.get("HECATE_IM_SLACK_TEST_MODE") != "1",
    )


__all__ = ["provider"]
