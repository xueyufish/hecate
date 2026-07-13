"""Install-time API surface validation for plugins.

Verifies that a loaded plugin instance has the expected methods
for its declared type, beyond the basic isinstance check.
"""

from __future__ import annotations

from typing import Any

from hecate.plugin.types import PLUGIN_TYPE_REGISTRY


def validate_api_surface(plugin_type: str, plugin_instance: Any) -> list[str]:
    """Validate that *plugin_instance* has the expected methods for *plugin_type*.

    Returns a list of validation error strings (empty = valid).
    """
    errors: list[str] = []

    abc_class = PLUGIN_TYPE_REGISTRY.get(plugin_type)
    if abc_class is None:
        errors.append(f"Unknown plugin type: {plugin_type!r}")
        return errors

    required_methods = _get_required_methods(plugin_type)
    for method_name in required_methods:
        if not hasattr(plugin_instance, method_name):
            errors.append(
                f"Plugin of type '{plugin_type}' must implement '{method_name}' (defined in {abc_class.__name__})"
            )
            continue

        method = getattr(plugin_instance, method_name)
        if not callable(method):
            errors.append(f"Attribute '{method_name}' on plugin of type '{plugin_type}' is not callable")

    return errors


def _get_required_methods(plugin_type: str) -> list[str]:
    """Return the required method names for each plugin type."""
    type_methods: dict[str, list[str]] = {
        "tool": ["execute"],
        "extension": [],  # All callbacks optional
        "trigger": [],  # Depends on trigger_type, checked at runtime
        "model": [],  # invoke/embed optional (may only implement one)
        "channel": ["receive", "respond", "stream"],
        "evaluator": ["evaluate"],
        "auth_provider": ["authenticate"],
        "secret_provider": [],  # Methods vary by implementation
    }
    return type_methods.get(plugin_type, [])
