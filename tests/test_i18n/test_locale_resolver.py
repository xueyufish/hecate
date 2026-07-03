"""Tests for LocaleResolver — locale detection priority chain."""

from __future__ import annotations

from hecate.i18n.locale_resolver import LocaleResolver


class TestLocaleResolver:
    def test_explicit_locale_wins(self) -> None:
        resolver = LocaleResolver()
        result = resolver.resolve(
            explicit_locale="zh-CN",
            accept_language="ja, en;q=0.9",
            user_locale="de",
            workspace_locale="fr",
        )
        assert result == "zh-CN"

    def test_accept_language_header(self) -> None:
        resolver = LocaleResolver()
        result = resolver.resolve(
            explicit_locale=None,
            accept_language="ja, en;q=0.9",
            user_locale="de",
            workspace_locale="fr",
        )
        assert result == "ja"

    def test_user_locale(self) -> None:
        resolver = LocaleResolver()
        result = resolver.resolve(
            explicit_locale=None,
            accept_language=None,
            user_locale="de",
            workspace_locale="fr",
        )
        assert result == "de"

    def test_workspace_locale(self) -> None:
        resolver = LocaleResolver()
        result = resolver.resolve(
            explicit_locale=None,
            accept_language=None,
            user_locale=None,
            workspace_locale="fr",
        )
        assert result == "fr"

    def test_system_default(self) -> None:
        resolver = LocaleResolver()
        result = resolver.resolve()
        assert result == "en"

    def test_empty_accept_language_falls_through(self) -> None:
        resolver = LocaleResolver()
        result = resolver.resolve(accept_language="")
        assert result == "en"

    def test_accept_language_with_quality(self) -> None:
        resolver = LocaleResolver()
        result = resolver.resolve(accept_language="zh-CN, en;q=0.9")
        assert result == "zh-cn"
