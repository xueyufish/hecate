"""Tests for namespace file roles and permissions audit (feature 5.5d)."""

from __future__ import annotations

from pathlib import Path

import yaml

from hecate.core.plugin.content_scanner import ContentScanner, classify_file
from hecate.core.plugin.dual_format import NAMESPACE_DIR_NAME


class TestClassifyNamespace:
    def test_manifest_and_payload_roles(self) -> None:
        assert classify_file(Path(f"{NAMESPACE_DIR_NAME}/plugin.yaml")) == "namespace-manifest"
        assert classify_file(Path(f"{NAMESPACE_DIR_NAME}/my_tool/__init__.py")) == "namespace-code"
        assert classify_file(Path(f"{NAMESPACE_DIR_NAME}/notes.txt")) == "namespace-code"

    def test_open_face_roles_unchanged(self) -> None:
        assert classify_file(Path("skills/a/SKILL.md")) == "skill"
        assert classify_file(Path("mcp.json")) == "mcp-credentials"
        assert classify_file(Path("other/plugin.yaml")) == "nested"


def _write_ns_package(root: Path, manifest: str, code: str) -> Path:
    ns = root / NAMESPACE_DIR_NAME
    ns.mkdir(parents=True)
    (ns / "plugin.yaml").write_text(manifest)
    (ns / "payload.py").write_text(code)
    return root


class TestNamespacePermissionsAudit:
    def test_dangerous_permission_flagged_as_manifest_role(self, tmp_path: Path) -> None:
        manifest = yaml.safe_dump({"type": "tool", "permissions": ["bash-*"]})
        result = ContentScanner().scan(_write_ns_package(tmp_path, manifest, "x = 1\n"))
        perm_findings = [f for f in result.findings if f["rule_id"] == "TOOLS-SHELL"]
        assert perm_findings and perm_findings[0]["file"].endswith("plugin.yaml")
        assert perm_findings[0]["severity"] == "medium"

    def test_malformed_permissions_produce_finding(self, tmp_path: Path) -> None:
        manifest = yaml.safe_dump({"type": "tool", "permissions": "network"})
        result = ContentScanner().scan(_write_ns_package(tmp_path, manifest, "x = 1\n"))
        assert any(f["rule_id"] == "PERM-MALFORMED" for f in result.findings)

    def test_clean_permissions_pass(self, tmp_path: Path) -> None:
        manifest = yaml.safe_dump({"type": "tool", "permissions": ["evaluator:basic"]})
        result = ContentScanner().scan(_write_ns_package(tmp_path, manifest, "x = 1\n"))
        assert result.findings == []
        assert result.verdict == "allow"

    def test_manifest_finding_outranks_code_finding(self, tmp_path: Path) -> None:
        # The same high-intrinsic secret in the manifest and in a payload
        # file: the manifest role caps at high, the code role at medium.
        token = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"
        manifest = yaml.safe_dump({"type": "tool", "description": f"key {token}"})
        result = ContentScanner().scan(_write_ns_package(tmp_path, manifest, f'TOKEN = "{token}"\n'))
        by_file = {f["file"]: f["severity"] for f in result.findings if f["rule_id"] == "SEC-github-token"}
        manifest_file = [k for k in by_file if k.endswith("plugin.yaml")]
        code_file = [k for k in by_file if k.endswith("payload.py")]
        assert manifest_file and code_file
        assert by_file[manifest_file[0]] == "high"
        assert by_file[code_file[0]] == "medium"
