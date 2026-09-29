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
- inventory schema version other than the current one (ERROR — run extract)
- invalid enum values for ``responsibility`` / ``implementation_mode``
  (ERROR); the explicit ``n/a`` marker is accepted for not-applicable
- governance fields missing (WARNING, reported per entry — promoted to
  ERROR under ``--strict``); ``maturity`` is never derived from
  ``status: delivered``
- research entries with all lifecycle governance fields missing (WARNING;
  strict fails); explicit ``pending`` is accepted. A research entry that
  flips to ``delivered`` without a catalog ✅ is blocked by the delivery
  contradiction rule above.

Field ownership
---------------

- The catalog (``docs/features/feature-catalog.md``) owns the index fields
  ``title`` / ``phase`` / ``category`` — refreshed by every extract run.
- The YAML inventory (``docs/features/feature-inventory.yaml``) owns the
  machine-readable status and governance data (``status``, ``maturity``,
  ``dependencies``, ``evidence``, ``acceptance``, ``responsibility``,
  ``implementation_mode``, ``provider_or_adapter``, ``enforcement_point``,
  ``state_owner``, ``milestone``, ``superseded_by``) plus any other
  per-entry keys — extract preserves them verbatim; new ids are seeded
  with empty governance fields.

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

# Current inventory schema version; extract writes it, check requires it.
_INVENTORY_SCHEMA_VERSION = 2

# Explicit markers accepted in place of a concrete value. ``n/a`` declares a
# field not applicable to the entry; ``pending`` declares a value deferred on
# purpose (research lifecycle fields). Both are "explicit", never debt.
_NOT_APPLICABLE = "n/a"
_PENDING = "pending"

_RESPONSIBILITY_VALUES = ("platform-guarantee", "shared-contract", "provider-guarantee", "optional-ecosystem")
_IMPLEMENTATION_MODE_VALUES = ("builtin", "in-process-plugin", "out-of-process", "external-service", "asset")

# Fields whose absence counts as governance debt on non-research entries.
# ``dependencies`` is seeded as a list (empty is a valid value), and
# ``evidence`` / ``acceptance`` keep their dedicated summarized warnings, so
# they are not repeated here.
_REQUIRED_GOVERNANCE_FIELDS = (
    "maturity",
    "responsibility",
    "implementation_mode",
    "provider_or_adapter",
    "enforcement_point",
    "state_owner",
    "milestone",
    "superseded_by",
)

_RESEARCH_STATUSES = {"research-candidate"}
_RESEARCH_LIFECYCLE_FIELDS = ("research_owner", "hypothesis", "investment_boundary", "review_trigger", "exit_decision")

# First-import seeds for the inventory-owned governance fields; extract
# seeds them only for new ids and never rewrites them on existing entries.
_DATE_PAREN_RE = re.compile(r"\s*\([^)]*\d{4}-\d{2}-\d{2}[^)]*\)\s*$")


def _normalize_id_cell(cell: str) -> str:
    """Strip the ✅ mark and date annotations from an ID cell.

    ``6.27 ✅ (2026-09-21)`` → ``6.27``; code suffixes like ``11.9 (D/T)``
    are kept — only parentheticals containing a calendar date are removed.
    """
    cleaned = cell.replace("✅", "")
    cleaned = _DATE_PAREN_RE.sub("", cleaned)
    return " ".join(cleaned.split())


_GOVERNANCE_FIELD_SEEDS: dict[str, Any] = {
    "maturity": None,
    "dependencies": [],
    "evidence": None,
    "acceptance": None,
    "responsibility": None,
    "implementation_mode": None,
    "provider_or_adapter": None,
    "enforcement_point": None,
    "state_owner": None,
    "milestone": None,
    "superseded_by": None,
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
#     responsibility / implementation_mode / provider_or_adapter
#     enforcement_point / state_owner / milestone / superseded_by
#     plus any other per-entry keys     (preserved verbatim; hand-edit here)
#   research-lifecycle fields (research_owner / hypothesis /
#     investment_boundary / review_trigger / exit_decision) apply to
#     status=research-candidate entries; explicit "pending" is accepted
#   conventions: "n/a" = explicitly not applicable (never debt);
#     "pending" = explicitly deferred; maturity is independent of status —
#     delivered never implies production; deferral and retirement live in
#     status, never in implementation_mode
# A catalog ✅ mark contradicting status=delivered (either direction) fails
# extract and check until resolved in this file (which owns status).
"""

# Managed-region markers embedded in governed markdown documents. Content
# between a begin/end pair is generated from the inventory; outside text is
# hand-written and never touched. ``strict="true"`` regions make check fail
# on drift; ``strict="false"`` regions only report it (migration period).
_MANAGED_BEGIN = re.compile(r"<!--\s*feature-inventory:managed:(?P<name>[a-z0-9:-]+)\s+(?P<attrs>[^>]*)begin\s*-->")
_MANAGED_END = re.compile(r"<!--\s*feature-inventory:managed:(?P<name>[a-z0-9:-]+)\s+end\s*-->")
_MANAGED_STRICT = re.compile(r'strict="(true|false)"')

# Known managed regions: name -> renderer. Registered by the govern step
# (task 4.1 renders entries and derived registration tables); unknown region
# names fail loudly so renames cannot strand content unmanaged.
_MANAGED_RENDERERS: dict[str, Any] = {}


def register_managed_region(name: str, renderer: Any) -> None:
    """Register a renderer for a managed region name (used by tests too)."""
    _MANAGED_RENDERERS[name] = renderer


# ---------------------------------------------------------------------------
# Governed registration tables (data-as-code; deterministic, PR-reviewable).
# These are per-capability/per-process registries, not per-feature data, so
# they live in scripts/feature_inventory_tables.yaml (loaded below) rather
# than in the feature inventory. Edit the YAML and re-run `sync`.
# ---------------------------------------------------------------------------

_TABLES_PATH = Path(__file__).resolve().parent / "feature_inventory_tables.yaml"


def _load_tables() -> dict[str, list[tuple[str, ...]]]:
    data = yaml.safe_load(_TABLES_PATH.read_text(encoding="utf-8"))
    return {name: [tuple(row) for row in rows] for name, rows in data.items()}


_TABLES = _load_tables()
_CONTRACT_REGISTRY = _TABLES["contract_registry"]
_CAPABILITY_DOMAINS = _TABLES["capability_domains"]
_ITERATIONS = _TABLES["iterations"]
_MILESTONES = _TABLES["milestones"]


def _render_dispositions(inventory: dict[str, Any]) -> str:
    """Governed disposition table: every entry with governance fields filled."""
    lines = [
        "Governed entries carry plan-§六 dispositions and plan-§一 governance five-tuples in the"
        " inventory (single source of truth). Unlisted entries keep explicit debt until their step fills them.",
        "",
        "| ID | Responsibility | Implementation | Enforcement point | State owner | Milestone |",
        "|---|---|---|---|---|---|",
    ]
    for entry in inventory.get("features", []):
        if not entry.get("milestone") or not entry.get("responsibility"):
            continue
        lines.append(
            f"| {entry['id']} | {entry['responsibility']} | {entry.get('implementation_mode') or '-'}"
            f" | {entry.get('enforcement_point') or '-'} | {entry.get('state_owner') or '-'}"
            f" | {entry['milestone']} |"
        )
    return "\n".join(lines) + "\n"


def _render_contract_registry(_inventory: dict[str, Any]) -> str:
    lines = [
        "Contract ownership, publish units, and state owners per replaceable capability (ADR-034/035).",
        "",
        "| Capability | Public contract | Publish unit | State owner | Current version / window |",
        "|---|---|---|---|---|",
    ]
    for row in _CONTRACT_REGISTRY:
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")
    lines.append(
        "In-process implementations ship with the main app; out-of-process implementations may release"
        " independently — having an SPI is not standalone-deployability."
    )
    return "\n".join(lines) + "\n"


def _render_capability_domains(_inventory: dict[str, Any]) -> str:
    lines = [
        "Capability-domain labels are ownership boundaries and future extraction candidates,"
        " not today's deployment units.",
        "",
        "| Domain | Current location | Target sub-package | Owns | Known gap |",
        "|---|---|---|---|---|",
    ]
    for row in _CAPABILITY_DOMAINS:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines) + "\n"


def _render_iterations(_inventory: dict[str, Any]) -> str:
    lines = [
        "Delivery iterations (execution order; each slice is independently acceptable). Full definitions:"
        " evolution plan §七.",
        "",
        "| Iteration | Scope | Work packages | Independently acceptable result | Out of scope this round |",
        "|---|---|---|---|---|",
    ]
    for row in _ITERATIONS:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines) + "\n"


def _render_milestones(_inventory: dict[str, Any]) -> str:
    lines = [
        "Stage gates. A milestone's exit conditions bind its covered steps; nothing earlier is promised.",
        "",
        "| Milestone | Covers | Exit conditions | Not yet promised |",
        "|---|---|---|---|",
    ]
    for row in _MILESTONES:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines) + "\n"


register_managed_region("catalog:dispositions", _render_dispositions)
register_managed_region("catalog:contract-registry", _render_contract_registry)
register_managed_region("catalog:capability-domains", _render_capability_domains)
register_managed_region("roadmap:iterations", _render_iterations)
register_managed_region("roadmap:milestones", _render_milestones)
register_managed_region("roadmap:capability-domains", _render_capability_domains)


def _render_managed_region(name: str, inventory: dict[str, Any]) -> str:
    renderer = _MANAGED_RENDERERS.get(name)
    if renderer is None:
        raise SystemExit(
            f"ERROR: unknown managed region {name!r} — no renderer registered;"
            " update the renderer registry in scripts/feature_inventory.py"
        )
    return renderer(inventory)


def _iter_managed_regions(text: str, source: Path) -> list[tuple[str, bool, int, int, int, int]]:
    """Locate begin/end marker pairs.

    Returns (name, strict, content_start, content_end, region_start, region_end)
    with begin/end lines included in region bounds. Unpaired markers fail.
    """
    regions: list[tuple[str, bool, int, int, int, int]] = []
    lines = text.splitlines(keepends=True)
    open_at: dict[str, tuple[int, bool]] = {}
    for lineno, line in enumerate(lines):
        begin = _MANAGED_BEGIN.search(line)
        end = _MANAGED_END.search(line)
        if begin:
            name = begin.group("name")
            if name in open_at:
                raise SystemExit(f"ERROR: {source}:{lineno + 1} nested/duplicate begin for managed region {name!r}")
            strict_attr = _MANAGED_STRICT.search(begin.group("attrs") or "")
            open_at[name] = (lineno, strict_attr is not None and strict_attr.group(1) == "true")
        elif end:
            name = end.group("name")
            if name not in open_at:
                raise SystemExit(f"ERROR: {source}:{lineno + 1} end marker without begin for managed region {name!r}")
            begin_line, strict = open_at.pop(name)
            content_start = sum(len(line_text) for line_text in lines[: begin_line + 1])
            content_end = sum(len(line_text) for line_text in lines[:lineno])
            regions.append((name, strict, content_start, content_end, 0, 0))
    if open_at:
        names = ", ".join(sorted(open_at))
        raise SystemExit(f"ERROR: {source} unterminated managed region(s): {names}")
    return regions


def sync_managed_regions(
    inventory: dict[str, Any],
    documents: dict[Path, str],
    *,
    write: bool,
) -> int:
    """Regenerate managed regions and report drift per entry+field.

    With ``write=True`` regions are rewritten from the inventory (idempotent;
    text outside regions is preserved byte-for-byte). With ``write=False``
    nothing is written and drift is only reported; region-level ``strict``
    attributes decide whether drift is an error.
    """
    failures = 0
    for path, original in documents.items():
        regions = _iter_managed_regions(original, path)
        if not regions:
            continue
        result = original
        # Replace from the end so earlier offsets stay valid.
        for name, _strict, content_start, content_end, _rs, _re in sorted(regions, key=lambda r: -r[2]):
            rendered = _render_managed_region(name, inventory)
            current = result[content_start:content_end]
            if current != rendered:
                if write:
                    result = result[:content_start] + rendered + result[content_end:]
                else:
                    drift = _first_line_diff(current, rendered)
                    marker = "DRIFT" if _strict else "drift (report-only)"
                    print(f"{marker}: {path.name} [{name}] {drift}", file=sys.stderr)
                    if _strict:
                        failures += 1
        if write and result != original:
            path.write_text(result, encoding="utf-8")
            print(f"synced {len(regions)} managed region(s) -> {path}")
        elif write:
            print(f"managed regions already in sync -> {path}")
    return 1 if failures else 0


def _first_line_diff(current: str, rendered: str) -> str:
    """Human-pointing summary of where a region differs (not a full diff)."""
    cur_lines, ren_lines = current.splitlines(), rendered.splitlines()
    for idx in range(max(len(cur_lines), len(ren_lines))):
        cur = cur_lines[idx] if idx < len(cur_lines) else "<missing>"
        ren = ren_lines[idx] if idx < len(ren_lines) else "<missing>"
        if cur != ren:
            return f"line {idx + 1}: current={cur.strip()!r} expected={ren.strip()!r}"
    return "identical"


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

    text = catalog_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    # Managed regions render FROM the inventory — their table rows are
    # generated output, never extraction sources. Collect their line spans
    # and skip anything inside them.
    managed_lines: set[int] = set()
    open_at: dict[str, int] = {}
    for lineno, line in enumerate(lines, start=1):
        begin = _MANAGED_BEGIN.search(line)
        end = _MANAGED_END.search(line)
        if begin:
            open_at[begin.group("name")] = lineno
        elif end:
            name = end.group("name")
            begin_line = open_at.pop(name, None)
            if begin_line is not None:
                managed_lines.update(range(begin_line, lineno + 1))

    in_pool_section = False
    for lineno, line in enumerate(lines, start=1):
        if lineno in managed_lines:
            continue
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
        # Two ✅ styles exist in the catalog: inside the Feature cell and
        # appended to the ID cell (optionally with a date suffix like
        # "✅ (2026-09-21)"). Normalize the ID cell so both parse; either
        # mark counts as the delivered mark.
        id_mark = "✅" in first
        first_normalized = _normalize_id_cell(first)
        if not _is_id_cell(first_normalized):
            continue
        feature_cell = cells[2]
        if feature_cell.startswith(":") or set(feature_cell) <= {"-"}:
            continue
        entry_id = first_normalized
        if entry_id in seen_ids:
            failures.append(f"{catalog_path.name}:{lineno} duplicate id {entry_id!r}")
            continue
        seen_ids.add(entry_id)
        delivered = id_mark or "✅" in feature_cell
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

    # Schema migration: seed governance fields that a pre-v2 entry lacks.
    # Filling the seed value (None / []) for an absent key never overwrites
    # an existing YAML-owned value, keeps output shape uniform per schema
    # version, and stays idempotent.
    for entry in merged:
        for field, seed in _GOVERNANCE_FIELD_SEEDS.items():
            if field not in entry:
                entry[field] = list(seed) if isinstance(seed, list) else seed

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

    schema = inventory.get("schema")
    if schema != _INVENTORY_SCHEMA_VERSION:
        errors.append(
            f"inventory schema is {schema!r}; expected {_INVENTORY_SCHEMA_VERSION} —"
            " run `python scripts/feature_inventory.py extract` to migrate"
        )

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
        entry_id = entry["id"]
        for field, allowed in (
            ("responsibility", _RESPONSIBILITY_VALUES),
            ("implementation_mode", _IMPLEMENTATION_MODE_VALUES),
        ):
            value = entry.get(field)
            if value is not None and value not in (*allowed, _NOT_APPLICABLE):
                errors.append(f"{entry_id}: {field}={value!r} is not one of {list(allowed)} or {_NOT_APPLICABLE!r}")

    # Governance debt: missing fields are explicit debt (per-entry report,
    # never auto-filled); "n/a" is an explicit not-applicable, not debt.
    for entry in entries:
        if entry.get("status") in _RESEARCH_STATUSES:
            continue
        missing = [field for field in _REQUIRED_GOVERNANCE_FIELDS if entry.get(field) is None]
        if missing:
            warnings.append(f"{entry['id']}: missing governance fields (explicit debt): {', '.join(missing)}")

    # Research lifecycle: fields may be explicitly "pending", but the whole
    # block must not be silently absent. Promotion to delivered without a
    # catalog ✅ is already blocked by the delivery-contradiction rule below.
    for entry in entries:
        if entry.get("status") not in _RESEARCH_STATUSES:
            continue
        if all(entry.get(field) is None for field in _RESEARCH_LIFECYCLE_FIELDS):
            warnings.append(
                f"{entry['id']}: research entry missing all lifecycle governance fields"
                f" ({', '.join(_RESEARCH_LIFECYCLE_FIELDS)}); use explicit values or {_PENDING!r}"
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
        "schema": _INVENTORY_SCHEMA_VERSION,
        # Governance fields this inventory owns per entry: status, maturity,
        # dependencies, evidence, acceptance, responsibility,
        # implementation_mode, provider_or_adapter, enforcement_point,
        # state_owner, milestone, superseded_by (titles are extracted
        # indexes; research lifecycle fields ride on research entries).
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
    result = check_inventory(inventory, entries, strict=args.strict)
    if result != 0:
        return result
    # Governed-document drift: strict regions fail check, loose ones only
    # report (migration period). Runs after inventory validation passes.
    return sync_managed_regions(inventory, _governed_documents(), write=False)


_GOVERNED_DOC_PATHS = (
    REPO_ROOT / "docs" / "features" / "feature-catalog.md",
    REPO_ROOT / "docs" / "features" / "roadmap.md",
)


def _governed_documents() -> dict[Path, str]:
    return {path: path.read_text(encoding="utf-8") for path in _GOVERNED_DOC_PATHS if path.exists()}


def cmd_sync(
    args: argparse.Namespace,
    inventory_path: Path = INVENTORY_PATH,
) -> int:
    inventory = yaml.safe_load(inventory_path.read_text(encoding="utf-8"))
    documents = _governed_documents()
    if args.check:
        return sync_managed_regions(inventory, documents, write=False)
    return sync_managed_regions(inventory, documents, write=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Feature inventory tooling")
    sub = parser.add_subparsers(dest="command", required=True)
    extract_parser = sub.add_parser("extract", help="rebuild the YAML skeleton from the catalog")
    extract_parser.add_argument("--json-summary", action="store_true")
    extract_parser.set_defaults(func=cmd_extract)
    check_parser = sub.add_parser("check", help="validate the inventory against the catalog")
    check_parser.add_argument("--strict", action="store_true", help="treat warnings as errors")
    check_parser.set_defaults(func=cmd_check)
    sync_parser = sub.add_parser("sync", help="regenerate managed regions in governed documents from the inventory")
    sync_parser.add_argument("--check", action="store_true", help="report drift without writing (strict regions fail)")
    sync_parser.set_defaults(func=cmd_sync)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
