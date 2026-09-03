"""Channel registration — register channel adapters with PluginRegistry.

The function is split into two phases:

- ``register_channels`` (legacy) — registers only the empty placeholder list,
  preserved for back-compat with the existing test suite that imports it.
- ``register_im_channels`` — registers IM adapters (Feishu, Slack, ...) via
  the ``hecate.channel_providers`` entry-point group when the corresponding
  environment credentials are present.

Future IM platforms (DingTalk, WeCom, Telegram, etc.) ship as channel
plugin packages (``packages/channels/hecate-channel-*``) registering
under the same entry-point group — no core change required.
"""

from __future__ import annotations

import logging
import os

from hecate.channel.adapter import ChannelBase
from hecate.core.plugin.manifest import PluginManifest
from hecate.core.plugin.registry import PluginRegistry

logger = logging.getLogger(__name__)


def _register(registry: PluginRegistry, instance: ChannelBase) -> None:
    """Register a single ChannelBase instance under its ``name``."""
    manifest = PluginManifest(
        type="channel",
        name=instance.name,
        version="1.0.0",
        api_version="1.0",
        min_platform_version="0.6.0",
        description=instance.description,
    )
    registry.register(manifest, instance)


def register_channels(registry: PluginRegistry) -> int:
    """Register the empty list of always-on channel adapters.

    Returns the count of registered adapters. Kept for back-compat with
    the existing test suite; IM channels are now registered via
    :func:`register_im_channels`.
    """
    count = 0
    logger.info("Registered %d built-in non-IM channel adapters", count)
    return count


def register_im_channels(registry: PluginRegistry) -> int:
    """Register Feishu and Slack adapters when their credentials are present.

    Two registration routes, in order:

    1. **Entry-point route (PR5a)** — ``resolve_channel_providers()``
       scans ``hecate.channel_providers`` for names listed in
       ``settings.CHANNEL_PROVIDERS``. Factories read their own
       ``HECATE_IM_*`` env vars and return ``None`` when unconfigured,
       so an entry-point hit and "credentials configured" coincide.
    2. **Env-gated fallback** — the historical soft-import branches run
       only for names the resolver did not already register (e.g. an
       environment where the entry-point metadata is unavailable). They
       lazy-import the channel plugin packages extracted in PR5b. This
       keeps the boot path robust to partial configuration and preserves
       the historical log lines.

    Missing credentials are logged at INFO and the adapter is skipped
    without raising — this keeps the boot path robust to partial
    configuration.
    """
    count = 0
    registered: set[str] = set()

    # 1. Entry-point route (PR5a)
    try:
        from hecate.channel.resolver import resolve_channel_providers

        for name, adapter in resolve_channel_providers().items():
            _register(registry, adapter)
            registered.add(name)
            count += 1
            logger.info("Registered %s IM channel adapter", name)
    except Exception:  # noqa: BLE001
        logger.exception("Channel provider resolver failed; falling back to env-gated registration")

    # 2. Env-gated fallback — only for names the resolver did not register.

    # Feishu (Lark)
    feishu_app_id = os.environ.get("HECATE_IM_FEISHU_APP_ID")
    feishu_app_secret = os.environ.get("HECATE_IM_FEISHU_APP_SECRET")
    if "feishu" in registered:
        logger.debug("Feishu already registered via entry point; skipping env-gated branch")
    elif feishu_app_id and feishu_app_secret:
        try:
            from hecate_channel_feishu.channel import create_feishu_channel

            adapter = create_feishu_channel(
                app_id=feishu_app_id,
                app_secret=feishu_app_secret,
                encrypt_key=os.environ.get("HECATE_IM_FEISHU_ENCRYPT_KEY"),
                verification_token=os.environ.get("HECATE_IM_FEISHU_VERIFICATION_TOKEN"),
                transport=os.environ.get("HECATE_IM_FEISHU_TRANSPORT", "webhook"),
            )
            _register(registry, adapter)
            count += 1
            logger.info("Registered Feishu IM channel adapter")
        except ImportError:
            logger.info(
                "hecate-channel-feishu not installed; skipping Feishu IM channel. "
                "Install with: uv pip install hecate-channel-feishu"
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to register Feishu IM channel")
    else:
        logger.info("Feishu IM credentials not configured; skipping Feishu channel")

    # Slack
    slack_bot_token = os.environ.get("HECATE_IM_SLACK_BOT_TOKEN")
    slack_signing_secret = os.environ.get("HECATE_IM_SLACK_SIGNING_SECRET")
    if "slack" in registered:
        logger.debug("Slack already registered via entry point; skipping env-gated branch")
    elif slack_bot_token and slack_signing_secret:
        try:
            from hecate_channel_slack.channel import create_slack_channel

            # Test environments set HECATE_IM_SLACK_TEST_MODE=1 to skip the
            # auth.test round-trip that slack_bolt runs on construction.
            test_mode = os.environ.get("HECATE_IM_SLACK_TEST_MODE") == "1"
            adapter = create_slack_channel(
                bot_token=slack_bot_token,
                signing_secret=slack_signing_secret,
                app_token=os.environ.get("HECATE_IM_SLACK_APP_TOKEN"),
                token_verification_enabled=not test_mode,
            )
            _register(registry, adapter)
            count += 1
            logger.info("Registered Slack IM channel adapter")
        except ImportError:
            logger.info(
                "hecate-channel-slack not installed; skipping Slack IM channel. "
                "Install with: uv pip install hecate-channel-slack"
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to register Slack IM channel")
    else:
        logger.info("Slack IM credentials not configured; skipping Slack channel")

    return count
