"""MessageCatalog — loads and manages translations from JSON/YAML files.

Translations are loaded from ``locales/{locale}/{namespace}.json`` or
``locales/{locale}/{namespace}.yaml``. Supports nested key lookup and
parameter interpolation.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep merge two dicts, with override taking precedence."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _get_nested(data: dict[str, Any], key: str) -> Any:
    """Get a value from a nested dict using dot notation.

    Args:
        data: The dictionary to search.
        key: Dot-separated key path (e.g., "errors.not_found").

    Returns:
        The value if found, None otherwise.
    """
    parts = key.split(".")
    current = data
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


class MessageCatalog:
    """Manages translations loaded from JSON/YAML files.

    Usage::

        catalog = MessageCatalog(base_dir="locales")
        catalog.load("zh-CN", "common")
        value = catalog.get("zh-CN", "greeting")  # "你好"
    """

    def __init__(self, base_dir: str | Path = "locales") -> None:
        self._base_dir = Path(base_dir)
        self._translations: dict[str, dict[str, Any]] = {}  # locale -> merged translations

    def load(self, locale: str, namespace: str) -> bool:
        """Load translations from a file for a locale and namespace.

        Args:
            locale: The locale code (e.g., "zh-CN", "en").
            namespace: The translation namespace (e.g., "common", "errors").

        Returns:
            True if translations were loaded, False if file not found.
        """
        json_path = self._base_dir / locale / f"{namespace}.json"
        yaml_path = self._base_dir / locale / f"{namespace}.yaml"

        data: dict[str, Any] | None = None

        if json_path.exists():
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load %s: %s", json_path, e)
        elif yaml_path.exists():
            try:
                import yaml

                data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning("Failed to load %s: %s", yaml_path, e)

        if data is None:
            return False

        if locale not in self._translations:
            self._translations[locale] = {}
        self._translations[locale] = _deep_merge(self._translations[locale], data)
        logger.debug("Loaded translations for %s/%s", locale, namespace)
        return True

    def get(self, locale: str, key: str) -> str | None:
        """Get a translation value for a locale and key.

        Args:
            locale: The locale code.
            key: Dot-separated key path (e.g., "errors.not_found").

        Returns:
            The translation string, or None if not found.
        """
        translations = self._translations.get(locale)
        if translations is None:
            return None

        value = _get_nested(translations, key)
        return str(value) if value is not None else None

    def get_all(self, locale: str) -> dict[str, Any]:
        """Get all translations for a locale.

        Args:
            locale: The locale code.

        Returns:
            Dictionary of all translations for the locale.
        """
        return dict(self._translations.get(locale, {}))

    def available_locales(self) -> list[str]:
        """Return list of locales that have translations loaded."""
        return sorted(self._translations.keys())

    def set_translations(self, locale: str, namespace: str, data: dict[str, Any]) -> None:
        """Set translations directly (for API uploads).

        Args:
            locale: The locale code.
            namespace: The translation namespace.
            data: Translation data dictionary.
        """
        if locale not in self._translations:
            self._translations[locale] = {}
        self._translations[locale] = _deep_merge(self._translations[locale], data)
