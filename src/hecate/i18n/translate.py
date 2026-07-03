"""Translate — the t() function for translation lookup.

Provides a module-level ``t()`` function that uses LocaleResolver and
MessageCatalog to look up translations with parameter interpolation.
"""

from __future__ import annotations

import logging
from typing import Any

from hecate.i18n.catalog import MessageCatalog
from hecate.i18n.locale_resolver import LocaleResolver

logger = logging.getLogger(__name__)

# Module-level singletons
_resolver = LocaleResolver()
_catalog = MessageCatalog()


def get_catalog() -> MessageCatalog:
    """Return the module-level MessageCatalog instance."""
    return _catalog


def get_resolver() -> LocaleResolver:
    """Return the module-level LocaleResolver instance."""
    return _resolver


def set_catalog(catalog: MessageCatalog) -> None:
    """Replace the module-level MessageCatalog instance."""
    global _catalog
    _catalog = catalog


def set_resolver(resolver: LocaleResolver) -> None:
    """Replace the module-level LocaleResolver instance."""
    global _resolver
    _resolver = resolver


def t(
    key: str,
    locale: str | None = None,
    **params: Any,
) -> str:
    """Look up a translation key with optional parameter interpolation.

    Args:
        key: Dot-separated translation key (e.g., "errors.not_found").
        locale: Explicit locale override. If None, uses the resolver's
            default behavior (system default "en").
        **params: Parameters for string interpolation.
            ``t("greeting", name="Alice")`` with value "Hello, {name}"
            returns "Hello, Alice".

    Returns:
        The translated string with parameters interpolated. Falls back
        to the key itself if no translation is found.
    """
    resolved_locale = locale or _resolver.resolve()

    # Try the resolved locale
    value = _catalog.get(resolved_locale, key)

    # Fallback to system default
    if value is None and resolved_locale != "en":
        value = _catalog.get("en", key)

    # Fallback to the key itself
    if value is None:
        value = key

    # Parameter interpolation
    if params:
        try:
            value = value.format(**params)
        except (KeyError, ValueError) as e:
            logger.debug("Parameter interpolation failed for key %s: %s", key, e)

    return value
