"""Plugin configuration — schema validation and runtime injection."""

from __future__ import annotations

import logging
from typing import Any

from jsonschema import validate

logger = logging.getLogger(__name__)


def validate_config(config: dict[str, Any], schema: dict[str, Any]) -> None:
    """Validate *config* against a JSON *schema*.

    Raises :class:`jsonschema.ValidationError` on failure.
    """
    validate(instance=config, schema=schema)


def inject_config(plugin_instance: Any, config: dict[str, Any]) -> None:
    """Inject configuration into a plugin instance.

    If the plugin implements ``on_config_change``, it is called with the
    new config dict. Otherwise the dict is stored as ``_config`` on the
    instance.
    """
    if hasattr(plugin_instance, "on_config_change"):
        plugin_instance.on_config_change(config)
    else:
        plugin_instance._config = config
