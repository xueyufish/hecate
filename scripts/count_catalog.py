#!/usr/bin/env python
"""Single source of truth for feature-catalog / roadmap statistics.

Computes per-phase feature counts with the audited rule (see the catalog's
"Counting basis" note):

- Sections are the ``## P1:`` .. ``## P5:`` headings, ending at
  ``## Reference Platforms``.
- A feature row is a table line starting with ``| <digit>``.
- A row counts as delivered when the check mark appears in the ID column
  or the Feature column (check marks inside the description do not count).

Usage::

    python scripts/count_catalog.py            # print the computed table
    python scripts/count_catalog.py --check    # exit 1 if docs drifted

``--check`` compares the computed figures against the Statistics table in
``docs/features/feature-catalog.md`` and the Current State table in
``docs/features/roadmap.md``. CI (or pre-push, once wired) can run it to
keep the two docs from drifting apart again.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG = REPO_ROOT / "docs" / "features" / "feature-catalog.md"
ROADMAP = REPO_ROOT / "docs" / "features" / "roadmap.md"

SECTIONS = ("P1", "P2", "P3", "P4", "P5")
_ROW_RE = re.compile(r"^\| (\d)")


def compute(catalog_text: str) -> dict[str, tuple[int, int]]:
    """Return ``{phase: (done, total)}`` per the counting basis."""
    counts: dict[str, list[int]] = {p: [0, 0] for p in SECTIONS}
    section: str | None = None
    for line in catalog_text.splitlines():
        if line.startswith("## "):
            heading = line[3:]
            section = next((p for p in SECTIONS if heading.startswith(p + ":") or heading.startswith(p + " ")), None)
            continue
        if line.startswith("## Reference Platforms"):
            section = None
        if section is None or not _ROW_RE.match(line):
            continue
        cells = line.split("|")
        counts[section][1] += 1
        if len(cells) > 3 and ("✅" in cells[1] or "✅" in cells[2]):
            counts[section][0] += 1
    return {p: (d, t) for p, (d, t) in counts.items()}


def _parse_doc_totals(text: str) -> dict[str, tuple[int, int]]:
    """Extract claimed ``(total, done)`` per phase from a stats table.

    Tolerates both column orders used in the repo:
    catalog ``| **P4 ...** | <total> | ... | <done>/<total> ... |`` and
    roadmap ``| **P4 ...** | <total> | <done>/<total> ... |``.
    """
    claimed: dict[str, tuple[int, int]] = {}
    for line in text.splitlines():
        m = re.match(r"^\|\s*\*{0,2}(P[1-5])\b[^|]*\|\s*(\d+)\s*\|", line)
        if not m:
            continue
        phase, total = m.group(1), int(m.group(2))
        dm_candidates = re.findall(r"(\d+)/(\d+)", line)
        # Description cells contain feature-id-like "1.3.6/1.3.6a" noise;
        # the real done/total fraction has a denominator equal to the row total.
        dm = next((f for f in dm_candidates if int(f[1]) == total), None)
        if dm:
            claimed[phase] = (int(dm[0]), total)
    return claimed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify catalog + roadmap figures; exit 1 on drift")
    args = parser.parse_args()

    catalog_text = CATALOG.read_text(encoding="utf-8")
    computed = compute(catalog_text)

    total_done = sum(d for d, _ in computed.values())
    total_rows = sum(t for _, t in computed.values())
    for phase in SECTIONS:
        done, rows = computed[phase]
        print(f"{phase}: {done}/{rows}")
    print(f"Total: {total_done}/{total_rows}")

    if not args.check:
        return 0

    problems: list[str] = []

    claimed_catalog = _parse_doc_totals(catalog_text)
    claimed_roadmap = _parse_doc_totals(ROADMAP.read_text(encoding="utf-8")) if ROADMAP.exists() else {}

    for phase in SECTIONS:
        done, rows = computed[phase]
        for source, claimed in (("feature-catalog.md", claimed_catalog), ("roadmap.md", claimed_roadmap)):
            if phase not in claimed:
                problems.append(f"{source}: {phase} row not found in statistics table")
                continue
            c_done, c_total = claimed[phase]
            if (c_done, c_total) != (done, rows):
                problems.append(f"{source}: {phase} claims {c_done}/{c_total} but computed {done}/{rows}")

    if problems:
        print("\nDRIFT DETECTED — update the docs to match computed figures:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\ncheck OK: catalog and roadmap statistics match computed figures")
    return 0


if __name__ == "__main__":
    sys.exit(main())
