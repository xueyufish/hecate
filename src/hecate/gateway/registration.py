"""Channel registration — register built-in channel adapters with PluginRegistry.

The function is split into two phases:

- ``register_channels`` (legacy) — registers only the empty placeholder list,
  preserved for back-compat with the existing test suite that imports it.
- ``register_im_channels`` — registers Feishu and Slack adapters when the
  corresponding environment credentials are present.

Future IM platforms (DingTalk, WeCom, Telegram, etc.) follow the same
pattern: append a ``try`` block here with the appropriate credential
gating.
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

    Each registration is gated on its own set of environment variables so
    deployments can enable only the channels they have credentials for.
    Missing credentials are logged at INFO and the adapter is skipped
    without raising — this keeps the boot path robust to partial
    configuration.
    """
    count = 0

    # Feishu (Lark)
    feishu_app_id = os.environ.get("HECATE_IM_FEISHU_APP_ID")
    feishu_app_secret = os.environ.get("HECATE_IM_FEISHU_APP_SECRET")
    if feishu_app_id and feishu_app_secret:
        try:
            from hecate.channel.im.feishu import create_feishu_channel

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
                "lark_oapi not installed; skipping Feishu IM channel. Install with: uv pip install 'hecate[tools]'"
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to register Feishu IM channel")
    else:
        logger.info("Feishu IM credentials not configured; skipping Feishu channel")

    # Slack
    slack_bot_token = os.environ.get("HECATE_IM_SLACK_BOT_TOKEN")
    slack_signing_secret = os.environ.get("HECATE_IM_SLACK_SIGNING_SECRET")
    if slack_bot_token and slack_signing_secret:
        try:
            from hecate.channel.im.slack import create_slack_channel

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
                "slack_bolt not installed; skipping Slack IM channel. Install with: uv pip install 'hecate[tools]'"
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to register Slack IM channel")
    else:
        logger.info("Slack IM credentials not configured; skipping Slack channel")

    return count
