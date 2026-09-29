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


def _write_inventory(tmp_path: Path, features: list[dict[str, Any]]) -> Path:
    inventory = tmp_path / "feature-inventory.yaml"
    inventory.write_text(
        yaml.safe_dump({"schema": 1, "features": features}, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8",
    )
    return inventory


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
        "schema": 1,
        "features": [{**_hand_filled_entry("6.15"), "status": "research-candidate", "dependencies": []}],
    }

    exit_code = fi.check_inventory(inventory, entries, strict=False)

    # Missing evidence/acceptance only warn in non-strict mode (unchanged behavior).
    assert exit_code == 0
    assert "ERROR" not in capsys.readouterr().err
