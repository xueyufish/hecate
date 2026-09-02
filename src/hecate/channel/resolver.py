"""Channel provider resolver — discovers ``hecate.channel_providers`` entry points.

A messaging channel adapter (Feishu, Slack, a future DingTalk / WeCom /
Telegram / third-party package) plugs in by registering a zero-arg
``provider`` factory under the ``hecate.channel_providers`` entry-point
group. Unlike ``memory_providers`` / ``llm_providers`` (single-select —
one active backend), channels are inherently **multi-instance**: Feishu
and Slack run side by side, so the resolver returns a ``dict`` keyed by
entry-point name and ``settings.CHANNEL_PROVIDERS`` is a tuple.

Factory contract (duck-typed, no ABC beyond ``ChannelBase``):

- ``def provider() -> ChannelBase | None`` — reads its own configuration
  (env vars today) and returns a configured adapter, or ``None`` when
  unconfigured (the resolver skips ``None``, mirroring the
  auth/secret-provider factories).
- A raising factory is logged and skipped — one broken channel never
  blocks the others or the boot path.

The shipped channel adapters live in the plugin packages
``packages/channels/hecate-channel-{feishu,slack}`` (PR5b) and register
their entries under this group; the historical in-core entries were
retired when the packages were extracted. Third-party channels follow
the same route.
"""

from __future__ import annotations

import logging
from importlib.metadata import entry_points
from typing import Any

from hecate.channel.adapter import ChannelBase
from hecate.core.config import settings

logger = logging.getLogger(__name__)

_providers_cache: dict[str, ChannelBase] | None = None
_resolved: bool = False


def resolve_channel_providers() -> dict[str, ChannelBase]:
    """Return the configured channel providers as a name→adapter dict.

    Scans ``entry_points(group="hecate.channel_providers")``, keeps the
    entries whose ``name`` is listed in ``settings.CHANNEL_PROVIDERS``
    (default ``("feishu", "slack")``), and invokes each zero-arg factory.
    Factories returning ``None`` (unconfigured) or raising are skipped
    with a log line. The result is cached module-wide — first call decides
    for the process lifetime; use ``reset_channel_providers_cache()`` in
    tests.

    Returns:
        Dict mapping entry-point name (e.g., ``"slack"``) to the
        configured ``ChannelBase`` instance. Empty dict when nothing is
        configured — callers treat that as "no IM channels" and boot
        continues normally.
    """
    global _providers_cache, _resolved
    if _resolved:
        return _providers_cache or {}
    _resolved = True
    _providers_cache = {}

    wanted = set(settings.CHANNEL_PROVIDERS)
    try:
        eps = entry_points(group="hecate.channel_providers")
    except Exception as e:  # pragma: no cover — defensive, metadata DB corruption
        logger.warning("Channel provider entry-point scan failed: %s", e)
        return {}

    for ep in eps:
        if ep.name not in wanted:
            logger.debug("Skipping channel provider %r (not in settings.CHANNEL_PROVIDERS)", ep.name)
            continue
        try:
            instance = ep.load()()
        except Exception:
            logger.exception("Channel provider %r factory raised; skipping", ep.name)
            continue
        if instance is None:
            logger.info("Channel provider %r unconfigured; skipping", ep.name)
            continue
        _providers_cache[ep.name] = instance
        logger.info("Resolved channel provider %r via entry point", ep.name)

    return _providers_cache


def reset_channel_providers_cache() -> None:
    """Clear the resolver cache. Test-only."""
    global _providers_cache, _resolved
    _providers_cache = None
    _resolved = False


def im_channel_names() -> tuple[str, ...]:
    """Names of all IM channels routable through the message bus.

    Unions the historical hardcoded prefixes (DingTalk / WeCom / Telegram
    land as plugins later) with everything the resolver currently has
    configured, so a newly installed channel package routes correctly
    without editing ``gateway.py``. Degrades to the hardcoded tuple when
    the resolver itself fails.
    """
    fallback: Any = ("feishu", "slack", "dingtalk", "wecom", "telegram")
    try:
        resolved = resolve_channel_providers()
    except Exception:  # pragma: no cover — resolver is exception-safe; belt-and-braces
        return fallback
    return tuple(dict.fromkeys((*fallback, *resolved.keys())))


__all__ = ["im_channel_names", "reset_channel_providers_cache", "resolve_channel_providers"]
