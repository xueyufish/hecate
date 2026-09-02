"""Feishu channel entry-point factory.

Registered under the ``hecate.channel_providers`` group as ``feishu``.
Core discovers this entry point via ``importlib.metadata``; when
``HECATE_CHANNEL_PROVIDERS`` lists ``feishu`` (it does by default), the
resolver loads this factory and registers the returned adapter with the
plugin registry.

Third-party channel packages declare their own entry under the same group
with a distinct name and subclass ``ChannelBase`` from
``hecate.channel.adapter`` — the same contract, no registration code in
core.
"""

from __future__ import annotations

import os

from .channel import FeishuChannel, create_feishu_channel


def provider() -> FeishuChannel | None:
    """Zero-arg entry-point factory for ``hecate.channel_providers``.

    Reads its own ``HECATE_IM_FEISHU_*`` env configuration and returns a
    configured :class:`FeishuChannel`, or ``None`` when unconfigured — the
    resolver skips ``None`` without blocking boot.
    """
    app_id = os.environ.get("HECATE_IM_FEISHU_APP_ID")
    app_secret = os.environ.get("HECATE_IM_FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        return None
    return create_feishu_channel(
        app_id=app_id,
        app_secret=app_secret,
        encrypt_key=os.environ.get("HECATE_IM_FEISHU_ENCRYPT_KEY"),
        verification_token=os.environ.get("HECATE_IM_FEISHU_VERIFICATION_TOKEN"),
        transport=os.environ.get("HECATE_IM_FEISHU_TRANSPORT", "webhook"),
    )


__all__ = ["provider"]
