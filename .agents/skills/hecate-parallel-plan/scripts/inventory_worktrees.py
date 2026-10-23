#!/usr/bin/env python3
"""Collect local Git evidence for parallel planning without changing worktrees."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


def _git(repo: Path, *args: str) -> str:
    git = shutil.which("git")
    if git is None:
        raise FileNotFoundError("git is required on PATH")
    result = subprocess.run(  # noqa: S603 -- Fixed Git operations use argv without a shell.
        [git, "--no-optional-locks", "-C", str(repo), *args],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=True,
    )
    return result.stdout


def _paths(repo: Path, *args: str) -> list[str]:
    return sorted(filter(None, _git(repo, *args).split("\0")))


def _registered(repo: Path) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for field in _git(repo, "worktree", "list", "--porcelain", "-z").split("\0"):
        if field:
            key, _, value = field.partition(" ")
            current[key] = value
        elif current:
            entries.append(current)
            current = {}
    return entries


def _error(exc: Exception) -> str:
    if isinstance(exc, subprocess.CalledProcessError):
        return exc.stderr.strip() or str(exc)
    return str(exc)


def _inspect(entry: dict[str, str], base_sha: str) -> dict:
    path = Path(entry["worktree"])
    record: dict = {"path": str(path), "registration": entry}
    try:
        head = _git(path, "rev-parse", "HEAD").strip()
        branch = _git(path, "rev-parse", "--abbrev-ref", "HEAD").strip()
        merge_base = _git(path, "merge-base", base_sha, head).strip()
        behind, ahead = _git(path, "rev-list", "--left-right", "--count", f"{base_sha}...{head}").split()
        record.update(
            head=head,
            branch=branch,
            merge_base=merge_base,
            behind_main=int(behind),
            ahead_main=int(ahead),
            committed_paths=_paths(
                path, "diff", "--no-ext-diff", "--no-renames", "--name-only", "-z", merge_base, head
            ),
            staged_paths=_paths(path, "diff", "--no-ext-diff", "--no-renames", "--cached", "--name-only", "-z"),
            unstaged_paths=_paths(path, "diff", "--no-ext-diff", "--no-renames", "--name-only", "-z"),
            untracked_paths=_paths(path, "ls-files", "--others", "--exclude-standard", "-z"),
            recent_commits=_git(path, "log", "-12", "--format=%h %s", f"{base_sha}..{head}").splitlines(),
        )
        if _git(path, "rev-parse", "HEAD").strip() != head:
            record["error"] = "HEAD changed during collection; inspect this worktree again."
    except (OSError, subprocess.SubprocessError) as exc:
        record["error"] = _error(exc)
    return record


def collect(repo: Path, roots: list[Path], base_ref: str = "origin/main") -> dict:
    """Inventory registered worktrees and report extra directories or partial errors."""
    report: dict = {
        "collected_at": datetime.now(UTC).isoformat(),
        "repo": str(repo.resolve()),
        "base_ref": base_ref,
        "freshness": "Local refs only; this script does not fetch.",
        "future_scope": "Unknown unless supplied separately; file lists are not ownership locks.",
        "worktrees": [],
        "extra_directories": [],
        "errors": [],
    }
    try:
        base_sha = _git(repo, "rev-parse", "--verify", f"{base_ref}^{{commit}}").strip()
        report["base_sha"] = base_sha
        entries = _registered(repo)
        report["worktrees"] = [_inspect(entry, base_sha) for entry in entries if "bare" not in entry]
        known = {Path(entry["worktree"]).resolve() for entry in entries}
        common = Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()).resolve()
        for root in roots:
            if not root.is_dir():
                report["errors"].append(f"Requested root does not exist or is not a directory: {root}")
                continue
            for child in sorted(root.iterdir()):
                if not child.is_dir() or child.resolve() in known:
                    continue
                extra = {"path": str(child.resolve()), "kind": "unregistered_directory"}
                if (child / ".git").exists():
                    try:
                        child_common = Path(
                            _git(child, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
                        ).resolve()
                        extra["kind"] = "same_repository_unregistered" if child_common == common else "separate_clone"
                    except (OSError, subprocess.SubprocessError) as exc:
                        extra["error"] = _error(exc)
                report["extra_directories"].append(extra)
        if _git(repo, "rev-parse", "--verify", f"{base_ref}^{{commit}}").strip() != base_sha:
            report["errors"].append("Base ref changed during collection; collect again.")
    except (OSError, subprocess.SubprocessError) as exc:
        report["errors"].append(_error(exc))
    report["incomplete"] = bool(
        report["errors"] or any("error" in item for item in report["worktrees"] + report["extra_directories"])
    )
    return report


def main() -> int:
    """Print a JSON snapshot; return nonzero when any requested inspection fails."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument(
        "--root", type=Path, action="append", default=[], help="Additional directory to inspect (repeatable)"
    )
    parser.add_argument("--base-ref", default="origin/main")
    args = parser.parse_args()
    report = collect(args.repo, args.root, args.base_ref)
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(report["incomplete"])


if __name__ == "__main__":
    raise SystemExit(main())
