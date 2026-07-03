"""Channel registration — register built-in channel adapters with PluginRegistry."""

from __future__ import annotations

import logging

from hecate.channel.adapter import ChannelABC
from hecate.plugin.manifest import PluginManifest
from hecate.plugin.registry import PluginRegistry

logger = logging.getLogger(__name__)


def register_channels(registry: PluginRegistry) -> int:
    """Register built-in channel adapters with the plugin registry.

    Args:
        registry: The PluginRegistry to register channels with.

    Returns:
        Number of channels registered.
    """
    # Built-in channel adapters will be added here as they are implemented.
    # For now, this is a placeholder that establishes the registration pattern.
    channel_classes: list[type[ChannelABC]] = []

    count = 0
    for cls in channel_classes:
        try:
            # mypy infers the list as type[ChannelABC] (abstract);
            # all entries are concrete subclasses, so this is safe.
            instance = cls()
            manifest = PluginManifest(
                type="channel",
                name=instance.name,
                version="1.0.0",
                api_version="1.0",
                min_platform_version="0.6.0",
                description=instance.description,
            )
            registry.register(manifest, instance)
            count += 1
        except Exception:
            logger.exception("Failed to register channel %s", cls.__name__)

    logger.info("Registered %d built-in channel adapters", count)
    return count
