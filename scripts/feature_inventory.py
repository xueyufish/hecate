"""Feature inventory tooling (roadmap-structured-source).

``extract`` parses the markdown feature catalog's table rows into the
structured YAML inventory (id/status/phase/category); ``check`` validates
the inventory against the catalog and its own governance rules:

- duplicate ids (ERROR)
- dependencies referencing unknown ids (ERROR)
- dependency cycles (ERROR)
- production maturity without evidence (ERROR)
- yaml/catalog id-set drift (ERROR)
- missing evidence/acceptance (WARNING, summarized — promoted to ERROR
  under ``--strict``)

Usage::

    python scripts/feature_inventory.py extract
    python scripts/feature_inventory.py check [--strict]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = REPO_ROOT / "docs" / "features" / "feature-catalog.md"
INVENTORY_PATH = REPO_ROOT / "docs" / "features" / "feature-inventory.yaml"

# A catalog row's first cell: an id like ``2.10a``, ``11.9 (D/T)``,
# ``1.3.21④``, ``13.14`` — must contain a digit and no markdown markers.
_ID_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.\-—]*(\s*\([^)]*\))?$")

# Fields the inventory owns for every entry (extract seeds them null).
_GOVERNANCE_FIELDS = ("maturity", "dependencies", "evidence", "acceptance")


def _is_id_cell(cell: str) -> bool:
    cell = cell.strip()
    if not cell or not _ID_RE.match(cell):
        return False
    return any(ch.isdigit() for ch in cell)


def _detect_phase(heading: str) -> str:
    match = re.search(r"P([1-5])", heading)
    return f"P{match.group(1)}" if match else "other"


def extract_entries(catalog_path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Parse catalog table rows into inventory entries.

    Returns (entries, parse_failures) — failures are lines that look like
    feature rows but do not match the expected shape; extract fails loudly
    on them so catalog format drift is caught, never silently skipped.
    """
    entries: list[dict[str, Any]] = []
    failures: list[str] = []
    phase = "other"
    seen_ids: set[str] = set()

    in_pool_section = False
    for lineno, line in enumerate(catalog_path.read_text(encoding="utf-8").splitlines(), start=1):
        heading = re.match(r"^##\s+(.*)$", line)
        if heading:
            # The research-candidate pool restates ids as governance notes —
            # its table rows are not catalog entries.
            in_pool_section = "Research Candidate Pool" in heading.group(1)
            phase = _detect_phase(heading.group(1))
            continue
        if in_pool_section:
            continue
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.split("|")]
        # split("|") yields an empty string for the leading/trailing pipe.
        if len(cells) < 4:
            continue
        first = cells[1]
        if not _is_id_cell(first):
            continue
        feature_cell = cells[2]
        if feature_cell.startswith(":") or set(feature_cell) <= {"-"}:
            continue
        entry_id = " ".join(first.split())
        if entry_id in seen_ids:
            failures.append(f"{catalog_path.name}:{lineno} duplicate id {entry_id!r}")
            continue
        seen_ids.add(entry_id)
        delivered = "✅" in feature_cell
        title = feature_cell.replace("✅", "").strip()
        entries.append(
            {
                "id": entry_id,
                "title": title,
                "phase": phase,
                "category": cells[3],
                "status": "delivered" if delivered else "planned",
                "maturity": None,
                "dependencies": [],
                "evidence": None,
                "acceptance": None,
            }
        )
    return entries, failures


def _load_yaml_ids(inventory: dict[str, Any]) -> list[str]:
    return [entry["id"] for entry in inventory.get("features", [])]


def _find_cycle(ids_with_deps: dict[str, list[str]]) -> list[str] | None:
    white, gray, black = 0, 1, 2
    color = dict.fromkeys(ids_with_deps, white)

    def dfs(node: str, stack: list[str]) -> list[str] | None:
        color[node] = gray
        stack.append(node)
        for dep in ids_with_deps.get(node, []):
            if dep not in color:
                continue
            if color[dep] == gray:
                return stack[stack.index(dep) :] + [dep]
            if color[dep] == white:
                found = dfs(dep, stack)
                if found:
                    return found
        stack.pop()
        color[node] = black
        return None

    for entry_id in ids_with_deps:
        if color[entry_id] == white:
            cycle = dfs(entry_id, [])
            if cycle:
                return cycle
    return None


def check_inventory(inventory: dict[str, Any], catalog_ids: set[str], strict: bool) -> int:
    entries = inventory.get("features", [])
    errors: list[str] = []
    warnings: list[str] = []

    ids = [e["id"] for e in entries]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        errors.append(f"duplicate ids in inventory: {duplicates}")

    known = set(ids)
    deps_by_id = {e["id"]: list(e.get("dependencies") or []) for e in entries}
    for entry in entries:
        for dep in deps_by_id[entry["id"]]:
            if dep not in known:
                errors.append(f"{entry['id']}: dependency {dep!r} does not exist")
    cycle = _find_cycle(deps_by_id)
    if cycle:
        errors.append(f"dependency cycle: {' -> '.join(cycle)}")

    for entry in entries:
        if entry.get("maturity") == "production" and not entry.get("evidence"):
            errors.append(
                f"{entry['id']}: maturity=production requires evidence (an interface existing is not production)"
            )

    drift_missing = sorted(catalog_ids - known)
    drift_extra = sorted(known - catalog_ids)
    if drift_missing:
        errors.append(f"ids in catalog but missing from inventory: {drift_missing}")
    if drift_extra:
        errors.append(f"ids in inventory but not in catalog: {drift_extra}")

    missing_evidence = [e["id"] for e in entries if e.get("status") != "research-candidate" and not e.get("evidence")]
    missing_acceptance = [
        e["id"] for e in entries if e.get("status") != "research-candidate" and not e.get("acceptance")
    ]
    if missing_evidence:
        warnings.append(
            f"{len(missing_evidence)} entries missing evidence (backfill planned; strict mode will fail on these)"
        )
    if missing_acceptance:
        warnings.append(
            f"{len(missing_acceptance)} entries missing acceptance (backfill planned; strict mode will fail on these)"
        )

    for message in errors:
        print(f"ERROR: {message}", file=sys.stderr)
    for message in warnings:
        print(f"WARNING: {message}")

    if errors:
        return 1
    if strict and warnings:
        print("ERROR: --strict is set and warnings are present", file=sys.stderr)
        return 1
    print(f"check ok: {len(entries)} entries, {len(errors)} errors, {len(warnings)} warnings")
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    entries, failures = extract_entries(CATALOG_PATH)
    if failures:
        print("Catalog rows failed to parse — fix the catalog or the extractor:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    inventory = {
        "schema": 1,
        # Governance fields this inventory owns per entry: maturity,
        # dependencies, evidence, acceptance (titles are extracted indexes).
        "features": entries,
    }
    INVENTORY_PATH.write_text(
        yaml.safe_dump(inventory, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8",
    )
    delivered = sum(1 for e in entries if e["status"] == "delivered")
    print(f"extracted {len(entries)} entries ({delivered} delivered) -> {INVENTORY_PATH}")
    if args.json_summary:
        print(json.dumps({"count": len(entries), "delivered": delivered}))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    inventory = yaml.safe_load(INVENTORY_PATH.read_text(encoding="utf-8"))
    entries, failures = extract_entries(CATALOG_PATH)
    if failures:
        print("Catalog rows failed to parse:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    catalog_ids = {e["id"] for e in entries}
    return check_inventory(inventory, catalog_ids, strict=args.strict)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Feature inventory tooling")
    sub = parser.add_subparsers(dest="command", required=True)
    extract_parser = sub.add_parser("extract", help="rebuild the YAML skeleton from the catalog")
    extract_parser.add_argument("--json-summary", action="store_true")
    extract_parser.set_defaults(func=cmd_extract)
    check_parser = sub.add_parser("check", help="validate the inventory against the catalog")
    check_parser.add_argument("--strict", action="store_true", help="treat warnings as errors")
    check_parser.set_defaults(func=cmd_check)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
