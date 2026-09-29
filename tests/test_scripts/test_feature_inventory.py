"""G5 gate evidence: feature-inventory extract/check merge semantics.

Covers the four closure-evidence classes the plan requires for G5: pre/post
metadata preservation, contradiction failure (both directions), generation
idempotency, and delivery-status consistency. Uses synthetic catalogs and
inventories under tmp_path — the real repo files are never touched.
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = REPO_ROOT / "scripts" / "feature_inventory.py"
_SPEC = importlib.util.spec_from_file_location("feature_inventory", _SCRIPT)
assert _SPEC and _SPEC.loader, "feature_inventory.py must be loadable"
fi = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fi)


def _write_catalog(tmp_path: Path, rows: list[str]) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    catalog = tmp_path / "feature-catalog.md"
    body = "# Catalog\n\n## P1 Features\n\n" + "\n".join(rows) + "\n"
    catalog.write_text(body, encoding="utf-8")
    return catalog


def _write_inventory(tmp_path: Path, features: list[dict[str, Any]], schema: int = 2) -> Path:
    inventory = tmp_path / "feature-inventory.yaml"
    inventory.write_text(
        yaml.safe_dump({"schema": schema, "features": features}, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8",
    )
    return inventory


def _load_inventory(inventory_path: Path) -> dict[str, Any]:
    return yaml.safe_load(inventory_path.read_text(encoding="utf-8"))


def _args() -> argparse.Namespace:
    return argparse.Namespace(json_summary=False, strict=False)


def _read_features(inventory_path: Path) -> list[dict[str, Any]]:
    return yaml.safe_load(inventory_path.read_text(encoding="utf-8"))["features"]


def _hand_filled_entry(entry_id: str = "1.1") -> dict[str, Any]:
    return {
        "id": entry_id,
        "title": "Old Title",
        "phase": "P1",
        "category": "tool",
        "status": "delivered",
        "maturity": "verified",
        "dependencies": ["0.9"],
        "evidence": "PR #1",
        "acceptance": "scenario S1",
        "custom_note": "keep me",
    }


def test_extract_preserves_hand_maintained_fields(tmp_path: Path) -> None:
    catalog = _write_catalog(tmp_path, ["| 1.1 | Renamed Feature ✅ | tool |"])
    inventory = _write_inventory(tmp_path, [_hand_filled_entry()])

    exit_code = fi.cmd_extract(_args(), catalog_path=catalog, inventory_path=inventory)

    assert exit_code == 0
    entry = _read_features(inventory)[0]
    assert entry["title"] == "Renamed Feature"  # catalog-owned index refreshed
    # YAML-owned fields survive verbatim, including unknown extension keys.
    assert entry["status"] == "delivered"
    assert entry["maturity"] == "verified"
    assert entry["dependencies"] == ["0.9"]
    assert entry["evidence"] == "PR #1"
    assert entry["acceptance"] == "scenario S1"
    assert entry["custom_note"] == "keep me"
    assert "# Field ownership:" in inventory.read_text(encoding="utf-8")


def test_extract_first_import_seeds_governance_empty(tmp_path: Path) -> None:
    catalog = _write_catalog(tmp_path, ["| 1.1 | Kept ✅ | tool |", "| 1.2 | Fresh | ui |"])
    inventory = _write_inventory(tmp_path, [_hand_filled_entry("1.1")])

    exit_code = fi.cmd_extract(_args(), catalog_path=catalog, inventory_path=inventory)

    assert exit_code == 0
    features = _read_features(inventory)
    assert [e["id"] for e in features] == ["1.1", "1.2"]  # new id appended last
    fresh = features[1]
    assert fresh["status"] == "planned"  # no ✅ mark in the catalog row
    assert fresh["maturity"] is None
    assert fresh["dependencies"] == []
    assert fresh["evidence"] is None
    assert fresh["acceptance"] is None


def test_extract_keeps_entry_removed_from_catalog(tmp_path: Path, capsys) -> None:
    catalog = _write_catalog(tmp_path, ["| 1.1 | Kept ✅ | tool |"])
    removed = _hand_filled_entry("9.9")
    inventory = _write_inventory(tmp_path, [_hand_filled_entry("1.1"), removed])

    exit_code = fi.cmd_extract(_args(), catalog_path=catalog, inventory_path=inventory)

    assert exit_code == 0
    assert [e["id"] for e in _read_features(inventory)] == ["1.1", "9.9"]
    assert "9.9" in capsys.readouterr().out  # reported, never silently dropped


def test_extract_fails_on_delivery_contradiction_both_directions(tmp_path: Path, capsys) -> None:
    # Direction A: catalog ✅ but the inventory (which owns status) is not delivered.
    catalog_a = _write_catalog(tmp_path / "a", ["| 2.1 | Feat ✅ | tool |"])
    inventory_a = _write_inventory(tmp_path / "a", [{**_hand_filled_entry("2.1"), "status": "planned"}])
    before_a = inventory_a.read_bytes()
    assert fi.cmd_extract(_args(), catalog_path=catalog_a, inventory_path=inventory_a) == 1
    assert inventory_a.read_bytes() == before_a  # failure writes nothing
    assert "2.1" in capsys.readouterr().err

    # Direction B: catalog lost the ✅ but the inventory says delivered.
    catalog_b = _write_catalog(tmp_path / "b", ["| 2.2 | Feat | tool |"])
    inventory_b = _write_inventory(tmp_path / "b", [_hand_filled_entry("2.2")])
    before_b = inventory_b.read_bytes()
    assert fi.cmd_extract(_args(), catalog_path=catalog_b, inventory_path=inventory_b) == 1
    assert inventory_b.read_bytes() == before_b
    assert "2.2" in capsys.readouterr().err


def test_extract_allows_research_candidate_refinement(tmp_path: Path) -> None:
    catalog = _write_catalog(tmp_path, ["| 6.15 | Agentic RL | training |"])
    entry = {**_hand_filled_entry("6.15"), "status": "research-candidate"}
    inventory = _write_inventory(tmp_path, [entry])

    exit_code = fi.cmd_extract(_args(), catalog_path=catalog, inventory_path=inventory)

    assert exit_code == 0
    assert _read_features(inventory)[0]["status"] == "research-candidate"
    assert _read_features(inventory)[0]["evidence"] == "PR #1"


def test_extract_is_idempotent(tmp_path: Path) -> None:
    catalog = _write_catalog(tmp_path, ["| 1.1 | Feat ✅ | tool |", "| 1.2 | Other | ui |"])
    inventory = _write_inventory(tmp_path, [_hand_filled_entry()])

    assert fi.cmd_extract(_args(), catalog_path=catalog, inventory_path=inventory) == 0
    after_first = inventory.read_bytes()
    assert fi.cmd_extract(_args(), catalog_path=catalog, inventory_path=inventory) == 0
    assert inventory.read_bytes() == after_first


def test_extract_fails_loud_on_parse_failures_without_writing(tmp_path: Path) -> None:
    catalog = _write_catalog(tmp_path, ["| 1.1 | Feat ✅ | tool |", "| 1.1 | Duplicate ✅ | tool |"])
    inventory = _write_inventory(tmp_path, [_hand_filled_entry()])
    before = inventory.read_bytes()

    exit_code = fi.cmd_extract(_args(), catalog_path=catalog, inventory_path=inventory)

    assert exit_code == 1
    assert inventory.read_bytes() == before


def test_check_reports_contradiction_as_error(tmp_path: Path, capsys) -> None:
    catalog = _write_catalog(tmp_path, ["| 2.1 | Feat ✅ | tool |"])
    entries, failures = fi.extract_entries(catalog)
    assert not failures
    inventory = {"schema": 1, "features": [{**_hand_filled_entry("2.1"), "status": "planned"}]}

    exit_code = fi.check_inventory(inventory, entries, strict=False)

    assert exit_code == 1
    assert "2.1" in capsys.readouterr().err


def test_check_accepts_research_candidate_refinement(tmp_path: Path, capsys) -> None:
    catalog = _write_catalog(tmp_path, ["| 6.15 | Agentic RL | training |"])
    entries, failures = fi.extract_entries(catalog)
    assert not failures
    inventory = {
        "schema": 2,
        "features": [{**_hand_filled_entry("6.15"), "status": "research-candidate", "dependencies": []}],
    }

    exit_code = fi.check_inventory(inventory, entries, strict=False)

    # Missing evidence/acceptance only warn in non-strict mode (unchanged behavior).
    assert exit_code == 0
    assert "ERROR" not in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Schema v2: governance fields, enum validation, debt, research lifecycle
# ---------------------------------------------------------------------------


def test_check_rejects_wrong_schema_version(tmp_path: Path, capsys) -> None:
    catalog = _write_catalog(tmp_path, ["| 1.1 | Feat ✅ | tool |"])
    entries, _ = fi.extract_entries(catalog)
    inventory_path = _write_inventory(tmp_path, [_hand_filled_entry()], schema=1)

    assert fi.check_inventory(_load_inventory(inventory_path), entries, strict=False) == 1
    assert "schema" in capsys.readouterr().err


def test_check_rejects_invalid_enum_values(tmp_path: Path, capsys) -> None:
    catalog = _write_catalog(tmp_path, ["| 1.1 | Feat ✅ | tool |"])
    entries, _ = fi.extract_entries(catalog)
    _enum_fields = ("responsibility", "implementation_mode", "maturity")
    entry = {
        **_hand_filled_entry(),
        **{field: "n/a" for field in fi._REQUIRED_GOVERNANCE_FIELDS if field not in _enum_fields},
        "maturity": "verified",
        "responsibility": "we-do-everything",  # not in the enum
        "implementation_mode": "magic",  # not in the enum
    }
    inventory_path = _write_inventory(tmp_path, [entry])

    assert fi.check_inventory(_load_inventory(inventory_path), entries, strict=False) == 1
    err = capsys.readouterr().err
    assert "responsibility" in err
    assert "implementation_mode" in err


def test_check_accepts_explicit_not_applicable(tmp_path: Path, capsys) -> None:
    catalog = _write_catalog(tmp_path, ["| 1.1 | Feat ✅ | tool |"])
    entries, _ = fi.extract_entries(catalog)
    entry = {
        **_hand_filled_entry(),
        "dependencies": [],  # synthetic catalog has no 0.9 to depend on
        **{
            "responsibility": "platform-guarantee",
            "implementation_mode": "builtin",
            "provider_or_adapter": "n/a",
            "enforcement_point": "n/a",
            "state_owner": "n/a",
            "milestone": "n/a",
            "superseded_by": "n/a",
        },
    }
    inventory_path = _write_inventory(tmp_path, [entry])

    assert fi.check_inventory(_load_inventory(inventory_path), entries, strict=False) == 0
    out = capsys.readouterr()
    assert "explicit debt" not in out.out  # n/a is never debt


def test_check_reports_governance_debt_per_entry(tmp_path: Path, capsys) -> None:
    catalog = _write_catalog(tmp_path, ["| 1.1 | Feat ✅ | tool |"])
    entries, _ = fi.extract_entries(catalog)
    entry = {**_hand_filled_entry(), "dependencies": []}  # synthetic catalog has no 0.9
    inventory_path = _write_inventory(tmp_path, [entry])  # no v2 fields at all
    inventory = _load_inventory(inventory_path)

    # Non-strict: per-entry debt warning, exit 0.
    assert fi.check_inventory(inventory, entries, strict=False) == 0
    captured = capsys.readouterr()
    assert "1.1: missing governance fields" in captured.out
    assert "milestone" in captured.out

    # Strict: the same debt fails.
    assert fi.check_inventory(inventory, entries, strict=True) == 1


def test_check_does_not_derive_production_from_delivered(tmp_path: Path, capsys) -> None:
    catalog = _write_catalog(tmp_path, ["| 1.1 | Feat ✅ | tool |"])
    entries, _ = fi.extract_entries(catalog)
    entry = {**_hand_filled_entry(), "dependencies": [], "maturity": None}  # delivered, maturity unset
    inventory_path = _write_inventory(tmp_path, [entry])

    assert fi.check_inventory(_load_inventory(inventory_path), entries, strict=False) == 0
    captured = capsys.readouterr()
    # maturity shows up as missing-debt, never auto-filled to production.
    assert "production" not in captured.err
    assert "maturity" in captured.out


def test_check_research_lifecycle_all_missing_is_debt(tmp_path: Path, capsys) -> None:
    catalog = _write_catalog(tmp_path, ["| 6.15 | Agentic RL | training |"])
    entries, _ = fi.extract_entries(catalog)
    entry = {**_hand_filled_entry("6.15"), "status": "research-candidate", "dependencies": []}
    inventory_path = _write_inventory(tmp_path, [entry])

    assert fi.check_inventory(_load_inventory(inventory_path), entries, strict=False) == 0
    assert "lifecycle governance fields" in capsys.readouterr().out


def test_check_research_explicit_pending_passes(tmp_path: Path, capsys) -> None:
    catalog = _write_catalog(tmp_path, ["| 6.15 | Agentic RL | training |"])
    entries, _ = fi.extract_entries(catalog)
    entry = {
        **_hand_filled_entry("6.15"),
        "status": "research-candidate",
        "dependencies": [],
        "research_owner": "pending",
        "hypothesis": "RL improves tool selection",
        "investment_boundary": "sandboxed experiments only",
        "review_trigger": "quarterly review",
        "exit_decision": "pending",
    }
    inventory_path = _write_inventory(tmp_path, [entry])

    assert fi.check_inventory(_load_inventory(inventory_path), entries, strict=False) == 0
    out = capsys.readouterr()
    assert "lifecycle governance fields" not in out.out


def test_check_blocks_research_promotion_without_catalog_checkmark(tmp_path: Path, capsys) -> None:
    """A research entry flipping to delivered without a catalog ✅ is a
    delivery contradiction — the promotion gate required by the spec."""
    catalog = _write_catalog(tmp_path, ["| 6.15 | Agentic RL | training |"])  # no ✅
    entries, _ = fi.extract_entries(catalog)
    entry = {**_hand_filled_entry("6.15"), "status": "delivered"}
    inventory_path = _write_inventory(tmp_path, [entry])

    assert fi.check_inventory(_load_inventory(inventory_path), entries, strict=False) == 1
    assert "6.15" in capsys.readouterr().err


def test_extract_seeds_v2_fields_for_new_ids(tmp_path: Path) -> None:
    catalog = _write_catalog(tmp_path, ["| 1.1 | Kept ✅ | tool |", "| 1.2 | Fresh | ui |"])
    inventory = _write_inventory(tmp_path, [_hand_filled_entry("1.1")])

    assert fi.cmd_extract(_args(), catalog_path=catalog, inventory_path=inventory) == 0
    fresh = _read_features(inventory)[1]
    for field in fi._GOVERNANCE_FIELDS:
        assert field in fresh
    assert fresh["responsibility"] is None
    assert fresh["implementation_mode"] is None
    assert fresh["superseded_by"] is None
    text = inventory.read_text(encoding="utf-8")
    assert "responsibility / implementation_mode" in text  # v2 header ownership
    assert yaml.safe_load(text)["schema"] == 2


# ---------------------------------------------------------------------------
# Managed regions: deterministic generation, idempotency, per-field drift
# ---------------------------------------------------------------------------

_BEGIN_LOOSE = '<!-- feature-inventory:managed:demo-table strict="false" begin -->'
_BEGIN_STRICT = '<!-- feature-inventory:managed:demo-table strict="true" begin -->'
_END = "<!-- feature-inventory:managed:demo-table end -->"


def _region_renderer(inventory: dict[str, Any]) -> str:
    lines = ["| id | milestone |", "|---|---|"]
    for entry in inventory["features"]:
        lines.append(f"| {entry['id']} | {entry.get('milestone') or '-'} |")
    return "\n".join(lines) + "\n"


@pytest.fixture(autouse=True)
def _register_demo_region():
    fi.register_managed_region("demo-table", _region_renderer)
    yield
    fi._MANAGED_RENDERERS.pop("demo-table", None)


def _doc(tmp_path: Path, *, strict: bool, region_body: str, prose_before: str = "Intro prose.\n\n") -> Path:
    path = tmp_path / "governed.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    marker = _BEGIN_STRICT if strict else _BEGIN_LOOSE
    text = f"{prose_before}{marker}\n{region_body}{_END}\n\nTrailing prose.\n"
    path.write_text(text, encoding="utf-8")
    return path


def _inventory_for(ids: list[str]) -> dict[str, Any]:
    return {
        "schema": 2,
        "features": [{"id": i, "title": i, "milestone": "M-S" if i == "1.1" else None} for i in ids],
    }


def test_sync_writes_region_and_preserves_prose(tmp_path: Path) -> None:
    path = _doc(tmp_path, strict=False, region_body="stale content\n")
    inventory = _inventory_for(["1.1"])

    assert fi.sync_managed_regions(inventory, {path: path.read_text(encoding="utf-8")}, write=True) == 0
    text = path.read_text(encoding="utf-8")
    assert "stale content" not in text
    assert "| 1.1 | M-S |" in text
    assert text.startswith("Intro prose.\n\n")  # prose before untouched
    assert text.endswith("Trailing prose.\n")  # prose after untouched


def test_sync_is_idempotent_and_byte_stable(tmp_path: Path) -> None:
    path = _doc(tmp_path, strict=False, region_body="stale\n")
    inventory = _inventory_for(["1.1", "1.2"])

    assert fi.sync_managed_regions(inventory, {path: path.read_text(encoding="utf-8")}, write=True) == 0
    after_first = path.read_bytes()
    assert fi.sync_managed_regions(inventory, {path: path.read_text(encoding="utf-8")}, write=True) == 0
    assert path.read_bytes() == after_first
    assert fi.sync_managed_regions(inventory, {path: path.read_text(encoding="utf-8")}, write=False) == 0


def test_sync_check_reports_drift_field_level_loose(tmp_path: Path, capsys) -> None:
    path = _doc(tmp_path, strict=False, region_body="| id | milestone |\n|---|---|\n| 1.1 | WRONG |\n")
    inventory = _inventory_for(["1.1"])

    assert fi.sync_managed_regions(inventory, {path: path.read_text(encoding="utf-8")}, write=False) == 0
    err = capsys.readouterr().err
    assert "drift (report-only)" in err
    assert "WRONG" in err and "M-S" in err  # points at the differing values


def test_sync_check_strict_region_fails_on_drift(tmp_path: Path, capsys) -> None:
    path = _doc(tmp_path, strict=True, region_body="tampered\n")
    inventory = _inventory_for(["1.1"])

    assert fi.sync_managed_regions(inventory, {path: path.read_text(encoding="utf-8")}, write=False) == 1
    assert "DRIFT" in capsys.readouterr().err


def test_sync_unknown_region_name_fails_loud(tmp_path: Path) -> None:
    begin = '<!-- feature-inventory:managed:ghost strict="true" begin -->'
    text = f"{begin}\nx\n<!-- feature-inventory:managed:ghost end -->\n"
    path = tmp_path / "governed.md"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(SystemExit, match="unknown managed region"):
        fi.sync_managed_regions(_inventory_for(["1.1"]), {path: text}, write=False)


def test_sync_unterminated_region_fails_loud(tmp_path: Path) -> None:
    text = "prose\n" + _BEGIN_STRICT + "\nnever closed\n"
    path = tmp_path / "governed.md"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(SystemExit, match="unterminated"):
        fi.sync_managed_regions(_inventory_for(["1.1"]), {path: text}, write=False)


def test_check_reports_managed_drift_when_called_from_cmd_check(tmp_path: Path, capsys) -> None:
    """cmd_check integrates the sync drift scan for governed documents."""
    catalog = _write_catalog(tmp_path, ["| 1.1 | Feat ✅ | tool |"])
    entry = {**_hand_filled_entry(), "dependencies": [], "milestone": "M-S"}
    inventory_path = _write_inventory(tmp_path, [entry])
    doc = _doc(tmp_path / "docs", strict=True, region_body="tampered\n")

    original_paths = fi._GOVERNED_DOC_PATHS
    fi._GOVERNED_DOC_PATHS = (doc,)
    try:
        assert fi.cmd_check(_args(), catalog_path=catalog, inventory_path=inventory_path) == 1
    finally:
        fi._GOVERNED_DOC_PATHS = original_paths
    assert "DRIFT" in capsys.readouterr().err


def test_extract_parses_checkmark_in_id_cell(tmp_path: Path) -> None:
    """Rows like ``| 6.27 ✅ (2026-09-21) | Title | cat |`` were silently
    skipped before; the ✅ mark in the ID cell must count as delivered."""
    catalog = _write_catalog(tmp_path, ["| 6.27 ✅ (2026-09-21) | Browser Tool | tools |", "| 1.1 | Plain ✅ | tool |"])
    entries, failures = fi.extract_entries(catalog)

    assert failures == []
    by_id = {e["id"]: e for e in entries}
    assert set(by_id) == {"6.27", "1.1"}
    assert by_id["6.27"]["status"] == "delivered"
    assert by_id["6.27"]["title"] == "Browser Tool"  # ✅ not part of the title
    assert by_id["1.1"]["status"] == "delivered"
