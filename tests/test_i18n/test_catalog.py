"""Tests for MessageCatalog — JSON/YAML loading, nested keys, interpolation."""

from __future__ import annotations

from pathlib import Path

from hecate.i18n.catalog import MessageCatalog


class TestMessageCatalog:
    def test_load_json(self, tmp_path: Path) -> None:
        locale_dir = tmp_path / "en"
        locale_dir.mkdir()
        (locale_dir / "common.json").write_text('{"greeting": "Hello"}')

        catalog = MessageCatalog(base_dir=tmp_path)
        assert catalog.load("en", "common") is True
        assert catalog.get("en", "greeting") == "Hello"

    def test_load_missing_file(self, tmp_path: Path) -> None:
        catalog = MessageCatalog(base_dir=tmp_path)
        assert catalog.load("en", "missing") is False
        assert catalog.get("en", "missing") is None

    def test_nested_key_lookup(self, tmp_path: Path) -> None:
        locale_dir = tmp_path / "en"
        locale_dir.mkdir()
        (locale_dir / "common.json").write_text('{"errors": {"not_found": "Not found"}}')

        catalog = MessageCatalog(base_dir=tmp_path)
        catalog.load("en", "common")
        assert catalog.get("en", "errors.not_found") == "Not found"

    def test_missing_nested_key(self, tmp_path: Path) -> None:
        locale_dir = tmp_path / "en"
        locale_dir.mkdir()
        (locale_dir / "common.json").write_text('{"greeting": "Hello"}')

        catalog = MessageCatalog(base_dir=tmp_path)
        catalog.load("en", "common")
        assert catalog.get("en", "errors.not_found") is None

    def test_available_locales(self, tmp_path: Path) -> None:
        for locale in ["en", "zh-CN", "ja"]:
            locale_dir = tmp_path / locale
            locale_dir.mkdir()
            (locale_dir / "common.json").write_text("{}")

        catalog = MessageCatalog(base_dir=tmp_path)
        catalog.load("en", "common")
        catalog.load("zh-CN", "common")
        catalog.load("ja", "common")
        assert sorted(catalog.available_locales()) == ["en", "ja", "zh-CN"]

    def test_set_translations(self) -> None:
        catalog = MessageCatalog()
        catalog.set_translations("en", "common", {"greeting": "Hello"})
        assert catalog.get("en", "greeting") == "Hello"

    def test_get_all(self, tmp_path: Path) -> None:
        locale_dir = tmp_path / "en"
        locale_dir.mkdir()
        (locale_dir / "common.json").write_text('{"greeting": "Hello", "farewell": "Goodbye"}')

        catalog = MessageCatalog(base_dir=tmp_path)
        catalog.load("en", "common")
        all_translations = catalog.get_all("en")
        assert all_translations["greeting"] == "Hello"
        assert all_translations["farewell"] == "Goodbye"
