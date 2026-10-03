#!/usr/bin/env python3
"""Check the execution stack compatibility matrix against the workspace.

Reads ``docs/design/execution-stack-compatibility-matrix.md`` and asserts
that the version strings it declares for the supported wheel rows match
the actual package versions declared in the workspace ``pyproject.toml``
files. A package version bump without a matrix update fails here, so the
matrix cannot silently drift from reality.

Usage: ``python scripts/check_execution_stack_matrix.py`` (repo root).
Exit codes: 0 = consistent, 1 = drift or unreadable input (fail closed).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = REPO_ROOT / "docs" / "design" / "execution-stack-compatibility-matrix.md"
PACKAGES = {
    "hecate-runtime": REPO_ROOT / "packages" / "hecate-runtime" / "pyproject.toml",
    "hecate-runner": REPO_ROOT / "packages" / "hecate-runner" / "pyproject.toml",
}

_VERSION_LINE = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)
_MATRIX_ROW = re.compile(r"^\|.*`(\d+\.\d+\.\d+)`（wheel）.*`(\d+\.\d+\.\d+)`（wheel）.*\|")


def read_package_version(pyproject: Path) -> str:
    """Extract the package version from a pyproject.toml."""
    text = pyproject.read_text(encoding="utf-8")
    match = _VERSION_LINE.search(text)
    if match is None:
        raise ValueError(f"no version found in {pyproject}")
    return match.group(1)


def declared_matrix_versions(matrix_text: str) -> set[tuple[str, str]]:
    """Versions declared for the supported wheel combos (runtime, runner)."""
    pairs: set[tuple[str, str]] = set()
    for line in matrix_text.splitlines():
        match = _MATRIX_ROW.match(line)
        if match is not None:
            pairs.add((match.group(1), match.group(2)))
    return pairs


def main() -> int:
    if not MATRIX_PATH.exists():
        print(f"FAIL: matrix document missing: {MATRIX_PATH}")
        return 1
    matrix_text = MATRIX_PATH.read_text(encoding="utf-8")

    actual_runtime = read_package_version(PACKAGES["hecate-runtime"])
    actual_runner = read_package_version(PACKAGES["hecate-runner"])
    declared = declared_matrix_versions(matrix_text)

    if not declared:
        print("FAIL: matrix declares no supported wheel version row")
        return 1
    if (actual_runtime, actual_runner) not in declared:
        print(
            f"FAIL: matrix drift — workspace has hecate-runtime {actual_runtime} / "
            f"hecate-runner {actual_runner}, but the matrix only declares: {sorted(declared)}. "
            "Update docs/design/execution-stack-compatibility-matrix.md."
        )
        return 1
    print(f"OK: matrix matches workspace (hecate-runtime {actual_runtime}, hecate-runner {actual_runner})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
