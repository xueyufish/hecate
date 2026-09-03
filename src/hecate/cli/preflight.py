"""Preflight CLI — `hecate preflight` checks readiness before deployment."""

from __future__ import annotations

import asyncio
import sys


def main() -> int:
    from hecate.ops.preflight import run_checks

    results = asyncio.run(run_checks())
    has_fail = False
    for r in results:
        status_icon = "PASS" if r.passed else r.level
        print(f"  [{status_icon}] {r.name}: {r.detail}")
        if not r.passed and r.level == "FAIL":
            has_fail = True
    if has_fail:
        print("\nPreflight FAILED.", file=sys.stderr)
        return 1
    print("\nPreflight PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
