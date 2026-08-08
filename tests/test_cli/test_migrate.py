"""Tests for hecate-migrate standalone CLI."""

from __future__ import annotations

import json
import sys

import pytest

import hecate.cli.migrate as migrate_mod


class TestCmdUpgrade:
    def test_upgrade_success(self, monkeypatch: pytest.MonkeyPatch, capsys):
        completed = type("Result", (), {"returncode": 0, "stdout": "Upgrading ...\n", "stderr": ""})()
        monkeypatch.setattr(migrate_mod.subprocess, "run", lambda *a, **kw: completed)
        rc = migrate_mod.cmd_upgrade()
        assert rc == 0
        captured = capsys.readouterr()
        assert "Upgrading" in captured.out

    def test_upgrade_failure(self, monkeypatch: pytest.MonkeyPatch, capsys):
        completed = type("Result", (), {"returncode": 1, "stdout": "", "stderr": "boom"})()
        monkeypatch.setattr(migrate_mod.subprocess, "run", lambda *a, **kw: completed)
        rc = migrate_mod.cmd_upgrade()
        assert rc == 1
        captured = capsys.readouterr()
        assert "boom" in captured.err


class TestCmdCheck:
    def test_no_pending_returns_0(self, monkeypatch: pytest.MonkeyPatch, capsys):
        def fake_run(*a, **kw):
            cmd = a[0]
            if cmd[1] == "current":
                return type("R", (), {"returncode": 0, "stdout": "abc1234 (head)\n", "stderr": ""})()
            return type("R", (), {"returncode": 0, "stdout": "abc1234 (head)\n", "stderr": ""})()

        monkeypatch.setattr(migrate_mod.subprocess, "run", fake_run)
        rc = migrate_mod.cmd_check()
        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out.strip())
        assert data["pending"] == 0
        assert data["current"] == "abc1234"

    def test_pending_returns_1(self, monkeypatch: pytest.MonkeyPatch, capsys):
        def fake_run(*a, **kw):
            cmd = a[0]
            if cmd[1] == "current":
                return type("R", (), {"returncode": 0, "stdout": "old_rev\n", "stderr": ""})()
            return type("R", (), {"returncode": 0, "stdout": "new_rev1 (head)\nnew_rev2 (head)\n", "stderr": ""})()

        monkeypatch.setattr(migrate_mod.subprocess, "run", fake_run)
        rc = migrate_mod.cmd_check()
        assert rc == 1
        captured = capsys.readouterr()
        data = json.loads(captured.out.strip())
        assert data["pending"] == 2


class TestCmdDowngrade:
    def test_downgrade_success(self, monkeypatch: pytest.MonkeyPatch):
        completed = type("Result", (), {"returncode": 0, "stdout": "ok\n", "stderr": ""})()
        monkeypatch.setattr(migrate_mod.subprocess, "run", lambda *a, **kw: completed)
        rc = migrate_mod.cmd_downgrade(steps=2)
        assert rc == 0


class TestMainCli:
    def test_no_args_runs_upgrade(self, monkeypatch: pytest.MonkeyPatch):
        called = {}
        monkeypatch.setattr(
            migrate_mod,
            "cmd_upgrade",
            lambda cwd=".": (called.setdefault("upgrade", True), 0)[1],
        )
        monkeypatch.setattr(sys, "argv", ["hecate-migrate"])
        rc = migrate_mod.main()
        assert called.get("upgrade") is True
        assert rc == 0

    def test_check_flag(self, monkeypatch: pytest.MonkeyPatch):
        called = {}
        monkeypatch.setattr(
            migrate_mod,
            "cmd_check",
            lambda cwd=".": (called.setdefault("check", True), 0)[1],
        )
        monkeypatch.setattr(sys, "argv", ["hecate-migrate", "--check"])
        migrate_mod.main()
        assert called.get("check") is True

    def test_downgrade_flag(self, monkeypatch: pytest.MonkeyPatch):
        called = {}
        monkeypatch.setattr(
            migrate_mod,
            "cmd_downgrade",
            lambda cwd=".", steps=0: (called.setdefault("downgrade", steps), 0)[1],
        )
        monkeypatch.setattr(sys, "argv", ["hecate-migrate", "--downgrade", "2"])
        migrate_mod.main()
        assert called.get("downgrade") == 2

    def test_expand_only_flag(self, monkeypatch: pytest.MonkeyPatch):
        called = {}
        monkeypatch.setattr(
            migrate_mod,
            "cmd_expand_only",
            lambda cwd=".": (called.setdefault("expand", True), 0)[1],
        )
        monkeypatch.setattr(sys, "argv", ["hecate-migrate", "--expand-only"])
        migrate_mod.main()
        assert called.get("expand") is True

    def test_contract_only_flag(self, monkeypatch: pytest.MonkeyPatch):
        called = {}
        monkeypatch.setattr(
            migrate_mod,
            "cmd_contract_only",
            lambda cwd=".": (called.setdefault("contract", True), 0)[1],
        )
        monkeypatch.setattr(sys, "argv", ["hecate-migrate", "--contract-only"])
        migrate_mod.main()
        assert called.get("contract") is True
