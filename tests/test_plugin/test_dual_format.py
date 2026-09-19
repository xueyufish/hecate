"""Tests for the dual-format namespace module (feature 5.5d)."""

from __future__ import annotations

from pathlib import Path

import pytest

from hecate.core.plugin.agent_plugins import AgentPluginValidationError
from hecate.core.plugin.dual_format import (
    AGENT_PLUGIN_NAMESPACE,
    NAMESPACE_DIR_NAME,
    build_plugin_json,
    check_namespace_extension_value,
    resolve_namespace,
    sanitize_package_name,
)


class TestNamespaceGuard:
    """Guard: the namespace constant is a pinned format identity (task 1.2)."""

    def test_namespace_constant_pinned(self) -> None:
        assert AGENT_PLUGIN_NAMESPACE == "io.github.xueyufish"

    def test_directory_name_matches_namespace(self) -> None:
        assert NAMESPACE_DIR_NAME == AGENT_PLUGIN_NAMESPACE


def _write_package(root: Path, plugin_json: dict, ns_manifest: dict | None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    import json

    (root / "plugin.json").write_text(json.dumps(plugin_json))
    if ns_manifest is not None:
        import yaml

        ns = root / NAMESPACE_DIR_NAME
        ns.mkdir(parents=True, exist_ok=True)
        (ns / "plugin.yaml").write_text(yaml.safe_dump(ns_manifest))
    return root


class TestResolveNamespace:
    def _plugin_json(self) -> dict:
        return {
            "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
            "name": "docs-helper",
            "version": "1.0.0",
        }

    def test_absent_namespace_returns_none(self, tmp_path: Path) -> None:
        assert resolve_namespace(tmp_path, self._plugin_json()) is None

    def test_valid_manifest_adopted(self, tmp_path: Path) -> None:
        ns = {"type": "tool", "entry": "python:docs_helper:DocsHelper", "permissions": ["network:https"]}
        resolved = resolve_namespace(_write_package(tmp_path, self._plugin_json(), ns), self._plugin_json())
        assert resolved is not None
        assert resolved.manifest.type == "tool"
        assert resolved.manifest.name == "docs-helper"
        assert resolved.manifest.version == "1.0.0"
        assert resolved.manifest.entry == "python:docs_helper:DocsHelper"
        assert resolved.manifest.permissions == ("network:https",)

    def test_identity_conflict_rejects(self, tmp_path: Path) -> None:
        ns = {"version": "2.0.0"}
        with pytest.raises(AgentPluginValidationError, match="conflicts with plugin.json"):
            resolve_namespace(_write_package(tmp_path, self._plugin_json(), ns), self._plugin_json())

    def test_matching_identity_passes(self, tmp_path: Path) -> None:
        ns = {"name": "docs-helper", "version": "1.0.0", "type": "tool", "entry": "python:docs_helper:DocsHelper"}
        resolved = resolve_namespace(_write_package(tmp_path, self._plugin_json(), ns), self._plugin_json())
        assert resolved is not None and resolved.manifest.entry.startswith("python:")

    def test_malformed_yaml_degrades(self, tmp_path: Path) -> None:
        from hecate.core.plugin.dual_format import NAMESPACE_DIR_NAME, NamespaceManifestError

        ns = tmp_path / NAMESPACE_DIR_NAME
        ns.mkdir(parents=True)
        (ns / "plugin.yaml").write_text("::: not yaml [")
        with pytest.raises(NamespaceManifestError):
            resolve_namespace(tmp_path, self._plugin_json())

    def test_unknown_field_degrades(self, tmp_path: Path) -> None:
        from hecate.core.plugin.dual_format import NamespaceManifestError

        with pytest.raises(NamespaceManifestError, match="outside the closed model"):
            resolve_namespace(
                _write_package(tmp_path, self._plugin_json(), {"python_payload": "x"}),
                self._plugin_json(),
            )

    def test_entry_without_type_degrades(self, tmp_path: Path) -> None:
        from hecate.core.plugin.dual_format import NamespaceManifestError

        with pytest.raises(NamespaceManifestError, match="must declare a type"):
            resolve_namespace(
                _write_package(tmp_path, self._plugin_json(), {"entry": "python:m:C"}),
                self._plugin_json(),
            )

    def test_malformed_permissions_degrade(self, tmp_path: Path) -> None:
        from hecate.core.plugin.dual_format import NamespaceManifestError

        with pytest.raises(NamespaceManifestError, match="permissions"):
            resolve_namespace(
                _write_package(tmp_path, self._plugin_json(), {"type": "tool", "permissions": "network"}),
                self._plugin_json(),
            )


class TestExtensionValueCheck:
    def test_non_object_value_warns(self) -> None:
        warnings: list[str] = []
        check_namespace_extension_value({AGENT_PLUGIN_NAMESPACE + "-x": 1}, warnings)  # other key untouched
        check_namespace_extension_value({"extensions": {AGENT_PLUGIN_NAMESPACE: "oops"}}, warnings)
        assert any("not an object" in w for w in warnings)

    def test_object_value_and_other_namespaces_silent(self) -> None:
        warnings: list[str] = []
        check_namespace_extension_value({"extensions": {AGENT_PLUGIN_NAMESPACE: {"v": 1}, "com.other": 3}}, warnings)
        assert warnings == []


class TestBuildPluginJson:
    def test_identity_moves_into_open_face(self) -> None:
        pj = build_plugin_json({"name": "my-tool", "version": "1.2.3", "description": "A tool"})
        assert pj["$schema"].endswith("/1.0.0/plugin.schema.json")
        assert pj["name"] == "my-tool"
        assert pj["version"] == "1.2.3"
        assert pj["description"] == "A tool"
        assert "author" not in pj

    def test_optional_fields(self) -> None:
        pj = build_plugin_json({"name": "x"}, author="me", homepage="https://x", repository="https://r")
        assert pj["author"] == {"name": "me"}
        assert pj["homepage"] == "https://x"
        assert pj["repository"] == "https://r"

    def test_requires_name(self) -> None:
        with pytest.raises(ValueError, match="name"):
            build_plugin_json({})


class TestSanitizePackageName:
    def test_conforming_name_unchanged(self) -> None:
        assert sanitize_package_name("deploy-helper") == "deploy-helper"

    def test_uppercase_and_underscores(self) -> None:
        assert sanitize_package_name("Deploy Helper_v2") == "deploy-helper-v2"

    def test_dup_runs_and_edges(self) -> None:
        assert sanitize_package_name("--a..b--") == "a-b"

    def test_leading_digit_allowed(self) -> None:
        assert sanitize_package_name("2fast") == "2fast"

    def test_leading_non_alnum_prefixed(self) -> None:
        assert sanitize_package_name("-fast") == "fast"
        assert sanitize_package_name(".run") == "run"

    def test_empty_falls_back(self) -> None:
        assert sanitize_package_name("///") == "plugin"

    def test_truncated_to_grammar_conforming_suffix(self) -> None:
        assert len(sanitize_package_name("a" * 100, max_length=64)) <= 64


class TestRootDiscoveryExcludesDualFormat:
    """Dual-format packages load only via the enable projection (spec 3.2)."""

    def test_dual_format_package_not_discovered(self, tmp_path: Path) -> None:
        from hecate.core.plugin.loader import discover_plugins

        plugin_json = {
            "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
            "name": "dual-pkg",
            "version": "1.0.0",
        }
        _write_package(tmp_path / "dual-pkg", plugin_json, {"type": "tool", "entry": "python:m:C"})
        legacy = tmp_path / "legacy-pkg"
        legacy.mkdir()
        (legacy / "plugin.yaml").write_text("name: x\nversion: 1\ntype: tool\nentry: python:x:X\n")

        found = [p.parent.name for p in discover_plugins(tmp_path)]
        assert found == ["legacy-pkg"]
