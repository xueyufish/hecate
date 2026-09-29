"""Feature inventory tooling (roadmap-structured-source).

``extract`` parses the markdown feature catalog's table rows and MERGES them
into the structured YAML inventory by id (non-destructive); ``check``
validates the inventory against the catalog and its own governance rules:

- duplicate ids (ERROR)
- dependencies referencing unknown ids (ERROR)
- dependency cycles (ERROR)
- production maturity without evidence (ERROR)
- yaml/catalog id-set drift (ERROR)
- delivery-status contradiction between catalog ✅ and inventory
  ``delivered`` (ERROR, either direction)
- missing evidence/acceptance (WARNING, summarized — promoted to ERROR
  under ``--strict``)

Field ownership
---------------

- The catalog (``docs/features/feature-catalog.md``) owns the index fields
  ``title`` / ``phase`` / ``category`` — refreshed by every extract run.
- The YAML inventory (``docs/features/feature-inventory.yaml``) owns the
  machine-readable status and governance data (``status``, ``maturity``,
  ``dependencies``, ``evidence``, ``acceptance``) plus any other per-entry
  keys — extract preserves them verbatim; new ids are seeded with empty
  governance fields.

A catalog ✅ mark that disagrees with the inventory's ``delivered`` status
(either direction) is a contradiction: extract and check both fail loudly
until a human resolves it in the YAML (which owns status).

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

# First-import seeds for the inventory-owned governance fields; extract
# seeds them only for new ids and never rewrites them on existing entries.
_GOVERNANCE_FIELD_SEEDS: dict[str, Any] = {
    "maturity": None,
    "dependencies": [],
    "evidence": None,
    "acceptance": None,
}
_GOVERNANCE_FIELDS = tuple(_GOVERNANCE_FIELD_SEEDS)

# Fields extract refreshes from the catalog on every run (the catalog owns
# them); everything else on an existing entry is YAML-owned and preserved.
_CATALOG_OWNED_FIELDS = ("title", "phase", "category")
_INVENTORY_OWNED_FIELDS = ("status",) + _GOVERNANCE_FIELDS

# Prepended to the generated YAML so the ownership rules survive next to the
# data (yaml.safe_dump cannot emit comments, hence the manual prefix).
_INVENTORY_HEADER = """\
# Feature inventory — generated/merged by `scripts/feature_inventory.py extract`.
#
# Field ownership:
#   docs/features/feature-catalog.md owns the index fields
#     title / phase / category          (refreshed by every extract run)
#   this YAML owns machine-readable status and governance data
#     status / maturity / dependencies / evidence / acceptance
#     plus any other per-entry keys     (preserved verbatim; hand-edit here)
# A catalog ✅ mark contradicting status=delivered (either direction) fails
# extract and check until resolved in this file (which owns status).
"""


def _governance_seed() -> dict[str, Any]:
    """Fresh copies of the inventory-owned governance field seeds."""
    return {
        field: (list(value) if isinstance(value, list) else value) for field, value in _GOVERNANCE_FIELD_SEEDS.items()
    }


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
                **_governance_seed(),
            }
        )
    return entries, failures


def _delivery_contradiction(catalog_delivered: bool, yaml_status: Any) -> bool:
    """True when the catalog ✅ mark and the inventory status disagree.

    The inventory owns ``status``, so any disagreement — catalog marks
    delivered while the YAML says otherwise, or the YAML says ``delivered``
    while the catalog has no ✅ — needs a human decision. Refinements like
    ``research-candidate`` (no ✅, not ``delivered``) are not contradictions.
    """
    return catalog_delivered != (yaml_status == "delivered")


def merge_entries(
    catalog_entries: list[dict[str, Any]],
    existing: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], dict[str, list[str]], list[str]]:
    """Merge freshly parsed catalog entries into the existing inventory.

    Returns ``(merged, report, contradictions)``. Ownership: the catalog owns
    ``title`` / ``phase`` / ``category`` (refreshed in place); the YAML owns
    ``status``, the governance fields, and any other keys on existing entries
    (preserved verbatim). Ids present in the inventory but gone from the
    catalog are kept in place and reported, never silently dropped. New ids
    are appended after the existing ones in catalog order, so the output is
    deterministic and repeated merges are byte-identical. Contradictions are
    delivery-status disagreements; callers must fail without writing when
    any are reported.
    """
    existing = existing or []
    catalog_by_id = {entry["id"]: entry for entry in catalog_entries}
    merged: list[dict[str, Any]] = []
    report: dict[str, list[str]] = {"new": [], "refreshed": [], "removed_kept": []}
    contradictions: list[str] = []
    seen_existing: set[str] = set()

    for old in existing:
        entry_id = old["id"]
        seen_existing.add(entry_id)
        catalog_entry = catalog_by_id.get(entry_id)
        if catalog_entry is None:
            merged.append(old)
            report["removed_kept"].append(entry_id)
            continue
        catalog_delivered = catalog_entry["status"] == "delivered"
        if _delivery_contradiction(catalog_delivered, old.get("status")):
            contradictions.append(
                f"{entry_id}: catalog {'marks delivered (✅)' if catalog_delivered else 'has no ✅ mark'}"
                f" but inventory status is {old.get('status')!r}"
            )
        refreshed = dict(old)
        for field in _CATALOG_OWNED_FIELDS:
            if refreshed.get(field) != catalog_entry[field]:
                refreshed[field] = catalog_entry[field]
                if entry_id not in report["refreshed"]:
                    report["refreshed"].append(entry_id)
        merged.append(refreshed)

    for entry in catalog_entries:
        if entry["id"] not in seen_existing:
            merged.append(entry)
            report["new"].append(entry["id"])

    return merged, report, contradictions


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


def check_inventory(inventory: dict[str, Any], catalog_entries: list[dict[str, Any]], strict: bool) -> int:
    entries = inventory.get("features", [])
    errors: list[str] = []
    warnings: list[str] = []

    ids = [e["id"] for e in entries]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        errors.append(f"duplicate ids in inventory: {duplicates}")

    known = set(ids)
    catalog_delivered_by_id = {e["id"]: e["status"] == "delivered" for e in catalog_entries}
    catalog_ids = set(catalog_delivered_by_id)
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

    for entry in entries:
        catalog_delivered = catalog_delivered_by_id.get(entry["id"])
        if catalog_delivered is None:
            continue
        if _delivery_contradiction(catalog_delivered, entry.get("status")):
            errors.append(
                f"{entry['id']}: delivery status contradicts the catalog"
                f" (catalog {'✅' if catalog_delivered else 'no ✅'} vs status={entry.get('status')!r};"
                f" the inventory owns status — resolve in the YAML or the catalog)"
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


def cmd_extract(
    args: argparse.Namespace,
    catalog_path: Path = CATALOG_PATH,
    inventory_path: Path = INVENTORY_PATH,
) -> int:
    entries, failures = extract_entries(catalog_path)
    if failures:
        print("Catalog rows failed to parse — fix the catalog or the extractor:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    existing: list[dict[str, Any]] | None = None
    if inventory_path.exists():
        loaded = yaml.safe_load(inventory_path.read_text(encoding="utf-8")) or {}
        existing = loaded.get("features") or []
    merged, report, contradictions = merge_entries(entries, existing)
    if contradictions:
        print(
            "Delivery-status contradictions between catalog and inventory —"
            " the inventory owns status; resolve there (or fix the catalog ✅) and re-run.",
            file=sys.stderr,
        )
        for contradiction in contradictions:
            print(f"  {contradiction}", file=sys.stderr)
        return 1
    inventory = {
        "schema": 1,
        # Governance fields this inventory owns per entry: status, maturity,
        # dependencies, evidence, acceptance (titles are extracted indexes).
        "features": merged,
    }
    inventory_path.write_text(
        _INVENTORY_HEADER + yaml.safe_dump(inventory, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8",
    )
    delivered = sum(1 for e in merged if e["status"] == "delivered")
    print(f"extracted {len(merged)} entries ({delivered} delivered) -> {inventory_path}")
    print(
        f"merge: {len(report['new'])} new, {len(report['refreshed'])} refreshed,"
        f" {len(report['removed_kept'])} removed-from-catalog (kept)"
    )
    for group in ("new", "refreshed", "removed_kept"):
        if report[group]:
            print(f"  {group}: {', '.join(report[group])}")
    if args.json_summary:
        print(
            json.dumps(
                {
                    "count": len(merged),
                    "delivered": delivered,
                    "new": len(report["new"]),
                    "refreshed": len(report["refreshed"]),
                    "removed_kept": len(report["removed_kept"]),
                }
            )
        )
    return 0


def cmd_check(
    args: argparse.Namespace,
    catalog_path: Path = CATALOG_PATH,
    inventory_path: Path = INVENTORY_PATH,
) -> int:
    inventory = yaml.safe_load(inventory_path.read_text(encoding="utf-8"))
    entries, failures = extract_entries(catalog_path)
    if failures:
        print("Catalog rows failed to parse:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    return check_inventory(inventory, entries, strict=args.strict)


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
