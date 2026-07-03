"""Tests for t() translation function end-to-end."""

from __future__ import annotations

from pathlib import Path

from hecate.i18n.catalog import MessageCatalog
from hecate.i18n.locale_resolver import LocaleResolver
from hecate.i18n.translate import set_catalog, set_resolver, t


class TestTranslateFunction:
    def test_simple_lookup(self, tmp_path: Path) -> None:
        locale_dir = tmp_path / "en"
        locale_dir.mkdir()
        (locale_dir / "common.json").write_text('{"greeting": "Hello"}')

        catalog = MessageCatalog(base_dir=tmp_path)
        catalog.load("en", "common")
        set_catalog(catalog)
        set_resolver(LocaleResolver())

        assert t("greeting", locale="en") == "Hello"

    def test_parameter_interpolation(self, tmp_path: Path) -> None:
        locale_dir = tmp_path / "en"
        locale_dir.mkdir()
        (locale_dir / "common.json").write_text('{"greeting": "Hello, {name}!"}')

        catalog = MessageCatalog(base_dir=tmp_path)
        catalog.load("en", "common")
        set_catalog(catalog)

        assert t("greeting", locale="en", name="Alice") == "Hello, Alice!"

    def test_missing_key_returns_key(self, tmp_path: Path) -> None:
        catalog = MessageCatalog(base_dir=tmp_path)
        set_catalog(catalog)

        assert t("nonexistent.key", locale="en") == "nonexistent.key"

    def test_fallback_to_system_default(self, tmp_path: Path) -> None:
        locale_dir = tmp_path / "en"
        locale_dir.mkdir()
        (locale_dir / "common.json").write_text('{"greeting": "Hello"}')

        catalog = MessageCatalog(base_dir=tmp_path)
        catalog.load("en", "common")
        set_catalog(catalog)

        assert t("greeting", locale="zh-CN") == "Hello"

    def test_nested_key(self, tmp_path: Path) -> None:
        locale_dir = tmp_path / "en"
        locale_dir.mkdir()
        (locale_dir / "common.json").write_text('{"errors": {"not_found": "Not found"}}')

        catalog = MessageCatalog(base_dir=tmp_path)
        catalog.load("en", "common")
        set_catalog(catalog)

        assert t("errors.not_found", locale="en") == "Not found"

    def test_interpolation_failure_graceful(self, tmp_path: Path) -> None:
        locale_dir = tmp_path / "en"
        locale_dir.mkdir()
        (locale_dir / "common.json").write_text('{"greeting": "Hello, {name}!"}')

        catalog = MessageCatalog(base_dir=tmp_path)
        catalog.load("en", "common")
        set_catalog(catalog)

        # Missing parameter — should return the template as-is
        result = t("greeting", locale="en")
        assert "Hello" in result
