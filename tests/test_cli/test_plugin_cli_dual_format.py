"""CLI-level tests for dual-format package/install/export commands (5.5d)."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from hecate.core.plugin import cli as cli_mod
from hecate.core.plugin.cli import main
from hecate.core.plugin.dual_format import NAMESPACE_DIR_NAME
from hecate.models.skill import SkillModel

WS = uuid.UUID("55555555-5555-5555-5555-555555555555")


def _legacy_plugin(root: Path) -> Path:
    (root / "tool_a").mkdir(parents=True)
    (root / "plugin.yaml").write_text(
        yaml.safe_dump(
            {"name": "tool-a", "version": "0.1.0", "type": "tool", "entry": "python:tool_a:ToolA"},
            sort_keys=False,
        )
    )
    (root / "tool_a" / "__init__.py").write_text("class ToolA:\n    pass\n")
    return root


def _run_cli(monkeypatch: pytest.MonkeyPatch, argv: list[str], capsys: pytest.CaptureFixture[str]) -> str:
    monkeypatch.setattr(sys, "argv", ["hecate-plugin", *argv])
    main()
    return capsys.readouterr().out


class TestInstallRouting:
    """`hecate plugin install` routes by layout (5.5d, task 5.3)."""

    def _dual_bundle(self, tmp_path: Path) -> Path:
        from hecate.core.plugin.packaging import bundle_dual_format, emit_dual_format

        tree = emit_dual_format(_legacy_plugin(tmp_path / "src"))
        return bundle_dual_format(tree, tmp_path / "tool-a.hecate-plugin")

    def test_dual_zip_routes_to_agent_pipeline(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import zipfile

        bundle = self._dual_bundle(tmp_path)
        with zipfile.ZipFile(bundle) as zf:
            assert "plugin.json" in zf.namelist()

        captured: dict[str, Any] = {}
        monkeypatch.setattr(cli_mod, "_agent_install", lambda **kw: captured.update(kw))
        args = SimpleNamespace(
            source="zip",
            location=str(bundle),
            ref=None,
            plugins_dir=str(tmp_path / "p"),
            workspace=None,
            installer=None,
        )
        cli_mod._run_install(args)
        assert captured["source_type"] == "dir"

    def test_legacy_zip_keeps_legacy_installer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from hecate.core.plugin.packaging import create_bundle

        bundle = create_bundle(_legacy_plugin(tmp_path / "src"))
        calls: dict[str, Any] = {}
        monkeypatch.setattr(
            "hecate.core.plugin.installer.install_plugin",
            lambda location, plugins_dir: calls.setdefault("name", "legacy"),
        )
        args = SimpleNamespace(
            source="zip",
            location=str(bundle),
            ref=None,
            plugins_dir=str(tmp_path / "p"),
            workspace=None,
            installer=None,
        )
        cli_mod._run_install(args)
        assert calls == {"name": "legacy"}

    def test_dual_dir_routes_to_agent_pipeline(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from hecate.core.plugin.packaging import emit_dual_format

        tree = emit_dual_format(_legacy_plugin(tmp_path / "src"))
        captured: dict[str, Any] = {}
        monkeypatch.setattr(cli_mod, "_agent_install", lambda **kw: captured.update(kw))
        args = SimpleNamespace(
            source="dir",
            location=str(tree),
            ref=None,
            plugins_dir=str(tmp_path / "p"),
            workspace=None,
            installer=None,
        )
        cli_mod._run_install(args)
        assert captured == {
            "source_type": "dir",
            "location": str(tree),
            "ref": None,
            "workspace": None,
            "installer": None,
        }


class TestPackageCommand:
    def test_package_emits_dual_format_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        src = _legacy_plugin(tmp_path / "src")
        out = _run_cli(monkeypatch, ["package", str(src)], capsys)
        assert "Created dual-format package directory" in out
        tree = tmp_path / "src.agentplugin"
        assert (tree / "plugin.json").is_file()
        assert (tree / NAMESPACE_DIR_NAME / "plugin.yaml").is_file()

    def test_package_zip_transport(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        src = _legacy_plugin(tmp_path / "src")
        bundle = tmp_path / "tool-a.hecate-plugin"
        out = _run_cli(monkeypatch, ["package", str(src), "--output", str(bundle)], capsys)
        assert "Created dual-format bundle" in out
        assert bundle.is_file()

    def test_package_invalid_dir_exits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit):
            _run_cli(monkeypatch, ["package", str(tmp_path / "nope")], capsys)


class _FakeSessionFactory:
    """Context-manager stand-in for async_session_factory (session-per-call)."""

    def __init__(self, session: Any) -> None:
        self._session = session

    def __call__(self) -> _FakeSessionFactory:
        return self

    async def __aenter__(self) -> Any:
        return self._session

    async def __aexit__(self, *exc: object) -> bool:
        return False


class TestExportCommand:
    async def test_export_cli_produces_bundle_and_zip(
        self,
        db_session: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        db_session.add(
            SkillModel(
                workspace_id=WS,
                name="deploy",
                description="Deploys",
                source="user",
                instructions="Run the deploy.",
            )
        )
        await db_session.flush()
        monkeypatch.setattr("hecate.core.database.async_session_factory", _FakeSessionFactory(db_session))

        def _invoke() -> str:
            # The CLI handler calls asyncio.run(); it must run on its own
            # thread because this test already drives an event loop.
            monkeypatch.setattr(
                sys, "argv", ["hecate-plugin", "export", "--workspace", str(WS), "--output", str(tmp_path), "--zip"]
            )
            main()
            return capsys.readouterr().out

        import asyncio

        out = await asyncio.get_running_loop().run_in_executor(None, _invoke)

        assert "Export plan" in out
        assert "Exported bundle" in out
        assert "Exported zip transport" in out
        bundle = tmp_path / "hecate-skills-55555555"
        assert (bundle / "plugin.json").is_file()
        assert (bundle / "skills" / "deploy" / "SKILL.md").is_file()
        assert (tmp_path / "hecate-skills-55555555.zip").is_file()

    def test_export_cli_error_exits(
        self, db_session: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr("hecate.core.database.async_session_factory", _FakeSessionFactory(db_session))
        with pytest.raises(SystemExit):
            _run_cli(monkeypatch, ["export", "--workspace", str(WS), "--output", str(tmp_path)], capsys)
        assert "Error" in capsys.readouterr().out
