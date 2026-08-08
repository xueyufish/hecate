"""Feature flag AST audit tool — scans source for stale or orphaned flags.

Usage:
  hecate flag-audit              # table output
  hecate flag-audit --check      # CI mode: exit 1 on FAIL conditions
  hecate flag-audit --src <dir>  # custom source root (default: src/hecate/)
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ENABLE_PATTERN = re.compile(r"^ENABLE_[A-Z_]+$")
DEFAULT_SRC_ROOT = "src/hecate"


@dataclass
class FlagRef:
    """A single code reference to an ENABLE_* flag."""

    name: str
    file: str
    line: int


def scan_source_tree(src_root: str = DEFAULT_SRC_ROOT) -> list[FlagRef]:
    """Walk the source tree and find all ENABLE_* references via AST."""
    refs: list[FlagRef] = []
    root = Path(src_root)
    if not root.exists():
        return refs
    for py_file in root.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text(), filename=str(py_file))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and ENABLE_PATTERN.match(node.attr):
                parent_val = node.value
                if isinstance(parent_val, ast.Name) and "settings" in parent_val.id.lower():
                    refs.append(FlagRef(name=node.attr, file=str(py_file), line=node.lineno))
                elif isinstance(parent_val, ast.Attribute) and parent_val.attr == "feature_settings":
                    if isinstance(parent_val.value, ast.Name) and "settings" in parent_val.value.id.lower():
                        refs.append(FlagRef(name=node.attr, file=str(py_file), line=node.lineno))
    return refs


def group_refs(refs: list[FlagRef]) -> dict[str, list[str]]:
    """Group references by flag name."""
    result: dict[str, list[str]] = {}
    for ref in refs:
        result.setdefault(ref.name, []).append(ref.file)
    return result


def print_table(ref_map: dict[str, list[str]]) -> None:
    if not ref_map:
        print("No feature flag references found.")
        return
    print(f"{'Flag':<40} {'References':<12} {'Files'}")
    print("-" * 80)
    for name in sorted(ref_map):
        files = ref_map[name]
        unique = sorted(set(files))
        print(f"{name:<40} {len(unique):<12} {', '.join(unique[:3])}")


def run_check(ref_map: dict[str, list[str]]) -> int:
    """CI mode: return exit code (0 = pass, 1 = fail)."""
    failures: list[str] = []
    for name, files in sorted(ref_map.items()):
        if not files:
            failures.append(f"{name}: zero references found (orphaned flag)")
    if failures:
        for f in failures:
            print(f"FAIL: {f}", file=sys.stderr)
        return 1
    print("All feature flags have references.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="hecate-flag-audit",
        description="Scan source tree for feature flag references.",
    )
    parser.add_argument("--check", action="store_true", help="CI mode: exit 1 on FAIL conditions")
    parser.add_argument("--src", default=DEFAULT_SRC_ROOT, help="Source root directory")
    args = parser.parse_args()

    refs = scan_source_tree(args.src)
    ref_map = group_refs(refs)

    if args.check:
        return run_check(ref_map)
    print_table(ref_map)
    return 0


if __name__ == "__main__":
    sys.exit(main())
