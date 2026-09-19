"""Tests for dual-format packaging output (feature 5.5d, task 5.1/5.2)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import yaml

from hecate.core.plugin.agent_plugins import validate_plugin_json
from hecate.core.plugin.dual_format import NAMESPACE_DIR_NAME
from hecate.core.plugin.packaging import bundle_dual_format, detect_bundle_layout, detect_layout, emit_dual_format


def _legacy_plugin(root: Path, *, with_skills: bool = False) -> Path:
    (root / "my_tool").mkdir(parents=True)
    (root / "plugin.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "my-tool",
                "version": "1.2.3",
                "type": "tool",
                "description": "My tool",
                "entry": "python:my_tool:MyTool",
                "permissions": ["network:https"],
            },
            sort_keys=False,
        )
    )
    (root / "my_tool" / "__init__.py").write_text("class MyTool:\n    pass\n")
    (root / "requirements.txt").write_text("httpx>=0.27\n")
    if with_skills:
        (root / "skills" / "greet").mkdir(parents=True)
        (root / "skills" / "greet" / "SKILL.md").write_text("---\nname: greet\ndescription: Greets\n---\nHi.")
    return root


class TestEmitDualFormat:
    def test_layout_structure(self, tmp_path: Path) -> None:
        src = _legacy_plugin(tmp_path / "src")
        out = emit_dual_format(src)

        assert out == tmp_path / "src.agentplugin"
        assert detect_layout(out) == "dual"
        plugin_json = json.loads((out / "plugin.json").read_text(encoding="utf-8"))
        assert plugin_json["name"] == "my-tool"
        assert plugin_json["version"] == "1.2.3"
        assert plugin_json["$schema"].endswith("/1.0.0/plugin.schema.json")

        ns_manifest = yaml.safe_load((out / NAMESPACE_DIR_NAME / "plugin.yaml").read_text(encoding="utf-8"))
        assert ns_manifest["type"] == "tool"
        assert ns_manifest["entry"] == "python:my_tool:MyTool"
        assert "name" not in ns_manifest and "version" not in ns_manifest

        assert (out / NAMESPACE_DIR_NAME / "my_tool" / "__init__.py").is_file()
        assert (out / NAMESPACE_DIR_NAME / "requirements.txt").is_file()
        assert not (out / "my_tool").exists()

    def test_open_face_validates_as_agent_plugins_package(self, tmp_path: Path) -> None:
        out = emit_dual_format(_legacy_plugin(tmp_path / "src", with_skills=True))
        result = validate_plugin_json(json.loads((out / "plugin.json").read_text(encoding="utf-8")))
        assert result.warnings == []
        assert (out / "skills" / "greet" / "SKILL.md").is_file()

    def test_empty_open_face_still_emits(self, tmp_path: Path) -> None:
        src = _legacy_plugin(tmp_path / "src")
        out = emit_dual_format(src)
        assert (out / "plugin.json").is_file()
        assert not (out / "skills").exists()
        assert not (out / "mcp.json").exists()
        assert detect_layout(out) == "dual"

    def test_rejects_directory_without_manifest(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        try:
            emit_dual_format(empty)
        except ValueError as e:
            assert "plugin.yaml" in str(e)
        else:
            raise AssertionError("expected ValueError")

    def test_zip_transport_unzips_to_valid_package(self, tmp_path: Path) -> None:
        out = emit_dual_format(_legacy_plugin(tmp_path / "src"))
        bundle = bundle_dual_format(out, tmp_path / "my-tool.hecate-plugin")
        assert detect_bundle_layout(bundle) == "dual"

        extracted = tmp_path / "unzipped"
        extracted.mkdir()
        with zipfile.ZipFile(bundle) as zf:
            zf.extractall(extracted)
        assert detect_layout(extracted) == "dual"
        assert validate_plugin_json(json.loads((extracted / "plugin.json").read_text(encoding="utf-8")))


class TestLegacyCompat:
    def test_detect_layout_on_legacy_tree(self, tmp_path: Path) -> None:
        assert detect_layout(_legacy_plugin(tmp_path / "src")) == "legacy"
        assert detect_layout(tmp_path / "nothing") == "unknown"

    def test_legacy_bundle_layout_detection(self, tmp_path: Path) -> None:
        from hecate.core.plugin.packaging import create_bundle

        src = _legacy_plugin(tmp_path / "src")
        bundle = create_bundle(src)
        assert detect_bundle_layout(bundle) == "legacy"
