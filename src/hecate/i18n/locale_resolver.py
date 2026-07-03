"""LocaleResolver — detects locale from multiple sources with priority chain.

Priority order:
1. Explicit locale parameter
2. Accept-Language HTTP header
3. User's preferred locale setting
4. Workspace default locale
5. System default locale ("en")
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_SYSTEM_DEFAULT = "en"


def _parse_accept_language(header: str) -> str | None:
    """Parse Accept-Language header and return the primary language tag.

    Args:
        header: The Accept-Language header value (e.g., "zh-CN, en;q=0.9").

    Returns:
        The primary language tag, or None if parsing fails.
    """
    if not header:
        return None
    # Take the first language tag (highest priority)
    match = re.match(r"([a-zA-Z]{2}(?:-[a-zA-Z]{2})?)", header.strip())
    return match.group(1).lower() if match else None


class LocaleResolver:
    """Resolves locale from multiple sources with priority chain.

    Usage::

        resolver = LocaleResolver()
        locale = resolver.resolve(
            explicit_locale="zh-CN",
            accept_language="ja, en;q=0.9",
            user_locale="de",
            workspace_locale="fr",
        )
        # Returns "zh-CN" (explicit parameter wins)
    """

    def resolve(
        self,
        explicit_locale: str | None = None,
        accept_language: str | None = None,
        user_locale: str | None = None,
        workspace_locale: str | None = None,
    ) -> str:
        """Resolve locale from the priority chain.

        Args:
            explicit_locale: Explicitly provided locale (highest priority).
            accept_language: Accept-Language HTTP header value.
            user_locale: User's preferred locale setting.
            workspace_locale: Workspace default locale setting.

        Returns:
            The resolved locale string (e.g., "zh-CN", "en", "ja").
        """
        if explicit_locale:
            return explicit_locale

        header_locale = _parse_accept_language(accept_language or "")
        if header_locale:
            return header_locale

        if user_locale:
            return user_locale

        if workspace_locale:
            return workspace_locale

        return _SYSTEM_DEFAULT
