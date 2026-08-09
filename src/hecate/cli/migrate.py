"""Standalone database migration entry point.

Separates Alembic migrations from the main application startup.
Used as a one-shot service in Docker Compose (hecate-migrate) before
the long-running hecate service starts, and as a K8s init container
or Helm pre-install hook.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def _alembic(*args: str, cwd: str | Path = ".") -> subprocess.CompletedProcess:
    """Run alembic with given args, returning CompletedProcess."""
    return subprocess.run(
        ["alembic", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _current_head(cwd: str | Path = ".") -> str:
    result = _alembic("current", cwd=cwd)
    if not result.stdout.strip():
        return "unknown"
    last_line = result.stdout.strip().split("\n")[-1].strip()
    if last_line.endswith("(head)"):
        last_line = last_line[: -len(" (head)")].strip()
    return last_line


def _heads(cwd: str | Path = ".") -> list[str]:
    result = _alembic("heads", cwd=cwd)
    if result.returncode != 0:
        return []
    lines = [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
    heads = []
    for line in lines:
        if " " in line:
            head_id = line.split(" ")[0]
            if head_id:
                heads.append(head_id)
    return heads


def cmd_upgrade(cwd: str | Path = ".") -> int:
    result = _alembic("upgrade", "head", cwd=cwd)
    if result.returncode != 0:
        sys.stderr.write(f"upgrade_failed: {result.stderr}\n")
        return 1
    sys.stdout.write(result.stdout)
    return 0


def cmd_check(cwd: str | Path = ".") -> int:
    current = _current_head(cwd)
    heads = _heads(cwd)
    pending = sum(1 for h in heads if h != current)
    output = {
        "pending": pending,
        "current": current,
        "heads": heads,
    }
    sys.stdout.write(json.dumps(output) + "\n")
    return 1 if pending > 0 else 0


def cmd_downgrade(cwd: str | Path = ".", steps: int = 1) -> int:
    result = _alembic("downgrade", f"-{steps}", cwd=cwd)
    if result.returncode != 0:
        sys.stderr.write(f"downgrade_failed: {result.stderr}\n")
        return 1
    sys.stdout.write(result.stdout)
    return 0


def cmd_expand_only(cwd: str | Path = ".") -> int:
    """Run only the expand-phase revisions. Requires expand branch labels."""
    result = _alembic("upgrade", "expand@head", cwd=cwd)
    if result.returncode != 0:
        sys.stderr.write(f"expand_upgrade_failed: {result.stderr}\n")
        return 1
    sys.stdout.write(result.stdout)
    return 0


def cmd_contract_only(cwd: str | Path = ".") -> int:
    """Run only the contract-phase revisions. Requires contract branch labels."""
    result = _alembic("upgrade", "contract@head", cwd=cwd)
    if result.returncode != 0:
        sys.stderr.write(f"contract_upgrade_failed: {result.stderr}\n")
        return 1
    sys.stdout.write(result.stdout)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="hecate-migrate",
        description="Standalone Alembic migration runner for Hecate.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only check for pending migrations; output JSON, exit 1 if pending.",
    )
    parser.add_argument(
        "--downgrade",
        type=int,
        metavar="N",
        help="Downgrade N revisions (default 1).",
    )
    parser.add_argument(
        "--expand-only",
        action="store_true",
        help="Run only expand-phase migrations.",
    )
    parser.add_argument(
        "--contract-only",
        action="store_true",
        help="Run only contract-phase migrations.",
    )
    parser.add_argument(
        "--cwd",
        default=".",
        help="Working directory for alembic.ini (default: current directory).",
    )

    args = parser.parse_args()

    if args.downgrade is not None:
        return cmd_downgrade(args.cwd, args.downgrade)
    if args.check:
        return cmd_check(args.cwd)
    if args.expand_only:
        return cmd_expand_only(args.cwd)
    if args.contract_only:
        return cmd_contract_only(args.cwd)
    return cmd_upgrade(args.cwd)


if __name__ == "__main__":
    sys.exit(main())
