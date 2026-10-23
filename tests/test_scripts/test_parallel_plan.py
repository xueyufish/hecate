"""Verify the parallel planning inventory against isolated real Git worktrees."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / ".agents/skills/hecate-parallel-plan/scripts/inventory_worktrees.py"
SPEC = importlib.util.spec_from_file_location("parallel_plan_inventory", SCRIPT)
assert SPEC and SPEC.loader
inventory = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inventory)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        encoding="utf-8",
        check=True,
    ).stdout.strip()


def _repository(root: Path) -> str:
    root.mkdir()
    _git(root, "init", "--initial-branch=main")
    _git(root, "config", "user.name", "Test Developer")
    _git(root, "config", "user.email", "developer@example.com")
    for name in ("contract.py", "staged.py", "unstaged.py", "old name.py"):
        (root / name).write_text("original\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "chore(test): seed repository")
    _git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    return _git(root, "rev-parse", "HEAD")


def test_inventory_includes_all_edits_and_outside_worktrees_without_mutation(tmp_path):
    """Dirty states, renames, Unicode paths, and outside-root branches stay visible."""
    repo = tmp_path / "repository with spaces"
    base = _repository(repo)
    root = tmp_path / "worktrees"
    root.mkdir()
    worker = root / "worker"
    outside = tmp_path / "outside root"
    _git(repo, "worktree", "add", "-b", "feat/worker", str(worker))
    _git(repo, "worktree", "add", "--detach", str(outside))
    (worker / "contract.py").write_text("new contract\n", encoding="utf-8")
    _git(worker, "add", "contract.py")
    _git(worker, "commit", "-m", "feat(test): change contract")
    (worker / "staged.py").write_text("staged\n", encoding="utf-8")
    _git(worker, "add", "staged.py")
    _git(worker, "mv", "old name.py", "renamed.py")
    (worker / "unstaged.py").write_text("unstaged\n", encoding="utf-8")
    (worker / "新文件.txt").write_text("untracked\n", encoding="utf-8")
    before = _git(worker, "status", "--porcelain=v1", "-z")

    report = inventory.collect(repo, [root])

    assert not report["incomplete"]
    assert report["base_sha"] == base
    records = {Path(item["path"]).resolve(): item for item in report["worktrees"]}
    assert set(records) == {repo.resolve(), worker.resolve(), outside.resolve()}
    record = records[worker.resolve()]
    assert record["branch"] == "feat/worker"
    assert record["merge_base"] == base
    assert record["ahead_main"] == 1 and record["behind_main"] == 0
    assert record["committed_paths"] == ["contract.py"]
    assert record["staged_paths"] == ["old name.py", "renamed.py", "staged.py"]
    assert record["unstaged_paths"] == ["unstaged.py"]
    assert record["untracked_paths"] == ["新文件.txt"]
    assert records[outside.resolve()]["branch"] == "HEAD"
    assert _git(worker, "status", "--porcelain=v1", "-z") == before
    assert _git(repo, "rev-parse", "origin/main") == base


def test_inventory_reports_branches_behind_main_and_separate_clones(tmp_path):
    """Pinned mainline divergence is not confused with unrelated clone contents."""
    repo = tmp_path / "repo"
    base = _repository(repo)
    root = tmp_path / "worktrees"
    root.mkdir()
    worker = root / "worker"
    _git(repo, "worktree", "add", "-b", "feat/worker", str(worker))
    (repo / "contract.py").write_text("main update\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "fix(test): update main")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(repo, "clone", "--no-hardlinks", str(repo), str(root / "clone"))
    (root / "ordinary directory").mkdir()

    report = inventory.collect(repo, [root])

    assert not report["incomplete"]
    record = next(item for item in report["worktrees"] if item["branch"] == "feat/worker")
    assert record["merge_base"] == base
    assert record["behind_main"] == 1 and record["ahead_main"] == 0
    assert record["committed_paths"] == []
    assert {item["kind"] for item in report["extra_directories"]} == {
        "separate_clone",
        "unregistered_directory",
    }


def test_missing_root_retains_worktree_evidence_and_marks_partial_result(tmp_path):
    """An invalid requested root cannot silently disappear from the result."""
    repo = tmp_path / "repo"
    _repository(repo)

    report = inventory.collect(repo, [tmp_path / "missing"])

    assert report["incomplete"]
    assert len(report["worktrees"]) == 1
    assert report["errors"]


def test_missing_registered_worktree_retains_error_and_healthy_entries(tmp_path):
    """Unavailable checkouts are reported rather than treated as conflict-free."""
    repo = tmp_path / "repo"
    _repository(repo)
    worker = tmp_path / "worker"
    _git(repo, "worktree", "add", "-b", "feat/worker", str(worker))
    worker.rename(tmp_path / "moved worker")

    report = inventory.collect(repo, [])

    assert report["incomplete"]
    assert len(report["worktrees"]) == 2
    assert sum("error" in item for item in report["worktrees"]) == 1


def test_cli_reports_missing_base_as_json_with_nonzero_exit(tmp_path):
    """CLI callers receive usable error evidence even when mainline is absent."""
    repo = tmp_path / "repo"
    _repository(repo)
    _git(repo, "update-ref", "-d", "refs/remotes/origin/main")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(repo)],
        capture_output=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["incomplete"] and report["errors"]
