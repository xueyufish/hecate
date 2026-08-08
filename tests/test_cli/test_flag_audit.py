"""Tests for feature flag AST audit tool."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

import hecate.cli.flag_audit as flag_audit


@pytest.fixture
def tmp_src(tmp_path: Path) -> Path:
    """Create a minimal source tree with ENABLE_* references."""
    src = tmp_path / "src"
    src.mkdir()
    pkg = src / "hecate"
    pkg.mkdir()

    (pkg / "config.py").write_text(
        textwrap.dedent("""\
        from __future__ import annotations

        class FeatureSettings:
            ENABLE_NEW_ENGINE: bool = True
            ENABLE_LEGACY_AUTH: bool = False
            ENABLE_UNUSED: bool = True
        """)
    )

    (pkg / "engine.py").write_text(
        textwrap.dedent("""\
        from __future__ import annotations

        from myapp.config import settings

        def get_engine():
            if settings.feature_settings.ENABLE_NEW_ENGINE:
                return "new"
            return "old"
        """)
    )

    (pkg / "auth.py").write_text(
        textwrap.dedent("""\
        from __future__ import annotations

        from myapp.config import settings

        def check_auth():
            if settings.ENABLE_LEGACY_AUTH:
                return "legacy"
            return "modern"
        """)
    )

    return src


class TestScanSourceTree:
    def test_finds_feature_settings_refs(self, tmp_src: Path):
        refs = flag_audit.scan_source_tree(str(tmp_src))
        names = [r.name for r in refs]
        assert "ENABLE_NEW_ENGINE" in names

    def test_finds_settings_refs(self, tmp_src: Path):
        refs = flag_audit.scan_source_tree(str(tmp_src))
        names = [r.name for r in refs]
        assert "ENABLE_LEGACY_AUTH" in names

    def test_orphaned_flag_not_referenced(self, tmp_src: Path):
        refs = flag_audit.scan_source_tree(str(tmp_src))
        names = [r.name for r in refs]
        assert "ENABLE_UNUSED" not in names

    def test_empty_dir_returns_empty(self, tmp_path: Path):
        refs = flag_audit.scan_source_tree(str(tmp_path / "nonexistent"))
        assert refs == []


class TestGroupRefs:
    def test_groups_by_name(self, tmp_src: Path):
        refs = flag_audit.scan_source_tree(str(tmp_src))
        grouped = flag_audit.group_refs(refs)
        assert "ENABLE_NEW_ENGINE" in grouped
        assert "ENABLE_LEGACY_AUTH" in grouped


class TestRunCheck:
    def test_no_failures_when_all_referenced(self, tmp_src: Path, monkeypatch: pytest.MonkeyPatch, capsys):
        refs = flag_audit.scan_source_tree(str(tmp_src))
        ref_map = flag_audit.group_refs(refs)
        monkeypatch.setattr(sys, "argv", ["hecate-flag-audit", "--src", str(tmp_src)])
        rc = flag_audit.run_check(ref_map)
        assert rc == 0


class TestMainCli:
    def test_table_output(self, tmp_src: Path, monkeypatch: pytest.MonkeyPatch, capsys):
        monkeypatch.setattr(sys, "argv", ["hecate-flag-audit", "--src", str(tmp_src)])
        rc = flag_audit.main()
        assert rc == 0
        captured = capsys.readouterr()
        assert "ENABLE_NEW_ENGINE" in captured.out
