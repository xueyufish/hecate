"""i18n — internationalization framework for Hecate.

Provides locale detection, message catalog management, and the ``t()``
translation function for multi-language support.
"""

from __future__ import annotations

from hecate.i18n.catalog import MessageCatalog
from hecate.i18n.locale_resolver import LocaleResolver
from hecate.i18n.translate import get_catalog, get_resolver, t

__all__ = [
    "LocaleResolver",
    "MessageCatalog",
    "get_catalog",
    "get_resolver",
    "t",
]
