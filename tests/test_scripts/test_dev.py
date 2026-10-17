"""Regression evidence for developer environments and shared worktree hooks."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("hecate_dev", REPO_ROOT / "scripts/dev.py")
assert SPEC and SPEC.loader
dev = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dev)


def _bash() -> str:
    git = shutil.which("git")
    if os.name == "nt" and git:
        bundled = Path(git).parent.parent / "bin/bash.exe"
        if bundled.is_file():
            return str(bundled)
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash is required for hook execution")
    return bash


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *arguments], text=True, capture_output=True, check=True)
    return result.stdout.strip()


def _repository(root: Path, *, branch: str = "feat/example") -> None:
    root.mkdir()
    _git(root, "init", "--initial-branch", branch)
    _git(root, "config", "user.name", "Test Developer")
    _git(root, "config", "user.email", "developer@example.com")
    scripts = root / "scripts"
    scripts.mkdir()
    for name in dev.HOOK_NAMES:
        (scripts / f"{name}.sh").write_text('#!/usr/bin/env bash\nprintf "original\\n"\n', encoding="utf-8")
    _git(root, "add", "scripts")
    _git(root, "commit", "-m", "chore(test): seed hook scripts")


@pytest.mark.parametrize("profile", ["dev", "postgres", "lint"])
def test_sync_uses_lock_and_profile_without_changing_another_environment(monkeypatch, tmp_path, profile):
    """All callers share package selection and isolate the target environment."""
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "some-other-worktree/.venv")
    monkeypatch.setattr(dev.shutil, "which", lambda _name: "uv")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 37)

    monkeypatch.setattr(dev.subprocess, "run", run)
    assert dev.sync_environment(check=True, profile=profile) == 37
    command, options = calls[0]
    assert "--locked" in command and "--check" in command
    assert options["cwd"] == tmp_path
    assert options["env"]["UV_PROJECT_ENVIRONMENT"] == str(tmp_path / ".venv")
    if profile == "lint":
        assert "--only-group" in command and "--package" not in command
    else:
        assert "hecate-enterprise" in command and "hecate-runner" in command
        assert ("postgres" in command) == (profile == "postgres")


@pytest.mark.parametrize("relative", [".venv/bin/python", ".venv/Scripts/python.exe"])
def test_interpreter_selection_is_worktree_local(monkeypatch, tmp_path, relative):
    """An activated environment elsewhere cannot redirect development checks."""
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    monkeypatch.setenv("VIRTUAL_ENV", "another-worktree")
    python = tmp_path / relative
    python.parent.mkdir(parents=True)
    python.touch()
    assert dev._python() == python


def test_subprocess_uses_repository_cwd_and_preserves_exit_code(monkeypatch, tmp_path):
    """Spaces and an unrelated caller directory do not change the execution root."""
    root = tmp_path / "worktree with spaces"
    root.mkdir()
    monkeypatch.setattr(dev, "ROOT", root)
    monkeypatch.chdir(tmp_path)
    probe = "import os,json,sys; print(json.dumps([os.getcwd(),os.environ['VIRTUAL_ENV']])); sys.exit(7)"
    result = dev._run([sys.executable, "-c", probe], capture=True)
    assert result.returncode == 7
    cwd, environment = json.loads(result.stdout)
    assert Path(cwd) == root
    assert Path(environment) == root / ".venv"


def test_typecheck_and_pytest_propagate_failures_without_expanding_scope(monkeypatch):
    """Local type scope includes packages; pytest arguments remain explicit."""
    monkeypatch.setattr(dev, "_python", lambda: Path(sys.executable))
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 5)

    monkeypatch.setattr(dev, "_run", run)
    assert dev.run_check("types") == 5
    assert calls[-1][-3:] == ["src/", "packages/", "scripts/dev.py"]
    assert dev.run_tests(["tests/test_scripts/test_dev.py", "-k", "case with spaces"]) == 5
    assert calls[-1][-3:] == ["tests/test_scripts/test_dev.py", "-k", "case with spaces"]
    calls.clear()
    assert dev.run_check("all") == 5
    assert len(calls) == 1
    with pytest.raises(dev.DevError, match="Specify pytest"):
        dev.run_tests([])


def test_doctor_reports_missing_packages_without_repairing(monkeypatch, tmp_path, capsys):
    """Diagnosis uses a read-only metadata probe and never invokes sync."""
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    monkeypatch.setattr(dev, "_python", lambda: Path(sys.executable))
    monkeypatch.setattr(dev, "check_hooks", lambda: 0)
    monkeypatch.setattr(dev.shutil, "which", lambda _name: "uv")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert kwargs["capture"]
        return subprocess.CompletedProcess(command, 0, json.dumps({"python": [3, 12], "missing": ["hecate-runner"]}))

    monkeypatch.setattr(dev, "_run", run)
    assert dev.doctor() == 1
    assert "hecate-runner" in capsys.readouterr().err
    assert len(calls) == 1 and calls[0][1:3] == ["-I", "-c"]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("original", [b"#!/usr/bin/env bash\necho custom\n", b"#!/usr/bin/env bash\n# \xff\n"])
def test_hooks_refuse_custom_scripts_and_preserve_explicit_replacements(monkeypatch, tmp_path, original):
    """A refusal cannot leave a partially installed set of dispatchers."""
    root = tmp_path / "repository"
    _repository(root)
    monkeypatch.setattr(dev, "ROOT", root)
    hooks = dev._hooks_directory()
    (hooks / "pre-push").write_bytes(original)
    with pytest.raises(dev.DevError, match="Existing hook"):
        dev.install_hooks()
    assert not (hooks / "pre-commit").exists()
    assert (hooks / "pre-push").read_bytes() == original
    assert dev.install_hooks(replace=True) == 0
    assert (hooks / "pre-push.before-hecate").read_bytes() == original
    assert dev.install_hooks() == 0
    assert dev.check_hooks() == 0


def test_shared_hooks_route_arguments_stdin_and_failure_to_current_worktree(monkeypatch, tmp_path):
    """The installed shim follows Git's invoking worktree, not its installer."""
    root = tmp_path / "first repository"
    _repository(root)
    monkeypatch.setattr(dev, "ROOT", root)
    dev.install_hooks()
    hook = dev._hooks_directory() / "pre-push"
    second = tmp_path / "second worktree"
    _git(root, "worktree", "add", "-b", "feat/second", str(second))
    (second / "scripts/pre-push.sh").write_text(
        '#!/usr/bin/env bash\nprintf "second:%s\\n" "$1"\ncat\nexit 9\n', encoding="utf-8"
    )
    result = subprocess.run(
        [_bash(), str(hook), "argument with spaces"],
        cwd=second,
        input="ref update\n",
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 9
    assert result.stdout.splitlines() == ["second:argument with spaces", "ref update"]


def test_hook_installation_honors_custom_git_hooks_path(monkeypatch, tmp_path):
    """Git configuration, rather than a guessed .git directory, owns the path."""
    root = tmp_path / "repository"
    _repository(root)
    _git(root, "config", "core.hooksPath", "custom hooks")
    monkeypatch.setattr(dev, "ROOT", root)
    dev.install_hooks()
    assert (root / "custom hooks/pre-commit").is_file()
    assert dev.check_hooks() == 0


def test_pre_commit_blocks_main_before_running_checks(monkeypatch, tmp_path):
    """The protected-branch guard remains active after dispatcher installation."""
    root = tmp_path / "repository"
    _repository(root, branch="main")
    for name in ("pre-commit.sh", "prevent-main-commit.sh"):
        shutil.copyfile(REPO_ROOT / "scripts" / name, root / "scripts" / name)
    (root / "scripts/dev.sh").write_text("echo unexpected > checks-ran\n", encoding="utf-8")
    monkeypatch.setattr(dev, "ROOT", root)
    dev.install_hooks()
    result = subprocess.run(
        ["git", "-C", str(root), "commit", "--allow-empty", "-m", "chore(test): protected branch probe"],
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert b"refusing to commit" in result.stderr
    assert not (root / "checks-ran").exists()


@pytest.mark.parametrize(
    "path",
    [
        "scripts/dev.sh",
        "scripts/pre-commit.sh",
        "scripts/commit-msg.sh",
        "scripts/pre-push.sh",
        ".envrc",
        "scripts/worktree-help.sh",
    ],
)
def test_shell_entrypoints_parse(path):
    """The checked-in shell entrypoints are valid in the supported shell."""
    subprocess.run([_bash(), "-n", str(REPO_ROOT / path)], check=True)


def test_ci_and_worktree_share_development_commands():
    """CI and bootstrap cannot silently acquire separate dependency lists."""
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    for job in ("lint", "typecheck", "test", "migrations", "step6-continuation-pg", "step6-pg-process-matrix"):
        commands = "\n".join(str(step.get("run", "")) for step in workflow["jobs"][job]["steps"])
        assert "python scripts/dev.py sync" in commands
        assert "uv sync --package" not in commands
    assert "bash scripts/dev.sh sync" in (REPO_ROOT / "scripts/worktree-help.sh").read_text(encoding="utf-8")
    assert "bash scripts/dev.sh sync --check" in (REPO_ROOT / ".envrc").read_text(encoding="utf-8")


def test_sync_rejects_stale_lock_without_rewriting_it(monkeypatch, tmp_path):
    """The real uv resolver cannot silently update a stale development lock."""
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("uv is required for locked-sync evidence")
    root = tmp_path / "locked workspace"
    root.mkdir()
    manifest = root / "pyproject.toml"
    text = (
        '[project]\nname = "hecate"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n'
        "[project.optional-dependencies]\ndev = []\n"
    )
    manifest.write_text(text, encoding="utf-8")
    subprocess.run([uv, "lock", "--offline", "--python", sys.executable], cwd=root, check=True, capture_output=True)
    original = (root / "uv.lock").read_bytes()
    manifest.write_text(text.replace('version = "0.1.0"', 'version = "0.2.0"'), encoding="utf-8")
    monkeypatch.setattr(dev, "ROOT", root)
    monkeypatch.setattr(dev, "WORKSPACE_PACKAGES", ("hecate",))
    monkeypatch.setenv("UV_OFFLINE", "1")
    assert dev.sync_environment() != 0
    assert (root / "uv.lock").read_bytes() == original


def test_pre_push_still_typechecks_without_a_rebase_target(tmp_path):
    """A missing origin/main cannot silently bypass the local type gate."""
    root = tmp_path / "repository"
    _repository(root)
    shutil.copyfile(REPO_ROOT / "scripts/pre-push.sh", root / "scripts/pre-push.sh")
    (root / "scripts/dev.sh").write_text('printf "%s %s" "$1" "$2" > gate-ran\nexit 6\n', encoding="utf-8")
    result = subprocess.run([_bash(), "scripts/pre-push.sh"], cwd=root, capture_output=True, check=False)
    assert result.returncode == 6
    assert (root / "gate-ran").read_text(encoding="utf-8") == "check types"


@pytest.mark.parametrize("bootstrap_exit", [0, 17])
def test_worktree_start_delegates_sync_and_never_reports_failed_bootstrap_ready(tmp_path, bootstrap_exit):
    """The public setup script runs in its target and propagates sync failure."""
    root = tmp_path / "repository"
    _repository(root, branch="main")
    for name in ("opsx-flow.sh", "worktree-help.sh"):
        target = root / "scripts" / name
        shutil.copyfile(REPO_ROOT / "scripts" / name, target)
        target.chmod(0o755)
    (root / "scripts/dev.sh").write_text(
        'printf "%s" "$1" > bootstrap-argument\npwd > bootstrap-cwd\n' + f"exit {bootstrap_exit}\n",
        encoding="utf-8",
    )
    _git(root, "add", "scripts")
    _git(root, "commit", "-m", "chore(test): prepare bootstrap fixture")
    parent = tmp_path / "worktrees with spaces"
    environment = {**os.environ, "HECATE_WORKTREE_ROOT": str(parent)}
    result = subprocess.run(
        [_bash(), "scripts/opsx-flow.sh", "start", "probe", "--no-tools"],
        cwd=root,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == bootstrap_exit, result.stderr
    target = parent / "probe"
    assert (target / "bootstrap-argument").read_text(encoding="utf-8") == "sync"
    assert ("Worktree ready:" in result.stdout) == (bootstrap_exit == 0)
    assert "probe" in (target / "bootstrap-cwd").read_text(encoding="utf-8")


def test_documentation_ci_uses_the_shared_lightweight_environment():
    """Documentation validation cannot silently install unpinned global tools."""
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/docs-check.yml").read_text(encoding="utf-8"))
    commands = "\n".join(str(step.get("run", "")) for step in workflow["jobs"]["docs-check"]["steps"])
    assert "python scripts/dev.py sync --profile lint" in commands
    assert "pip install" not in commands


def test_doctor_only_suggests_hook_repair_when_environment_is_complete(monkeypatch, capsys):
    """A hook-only diagnosis cannot send developers through an unrelated sync."""
    monkeypatch.setattr(dev, "_python", lambda: Path(sys.executable))
    monkeypatch.setattr(dev.shutil, "which", lambda _name: "uv")
    monkeypatch.setattr(
        dev,
        "_run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, '{"python":[3,12],"missing":[]}'),
    )

    def hooks():
        raise dev.DevError("Run: bash scripts/dev.sh hooks install")

    monkeypatch.setattr(dev, "check_hooks", hooks)
    assert dev.doctor() == 1
    errors = capsys.readouterr().err
    assert "hooks install" in errors
    assert "repair environment" not in errors
