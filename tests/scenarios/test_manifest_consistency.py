"""Manifest consistency checks for the scenario pack (task 1.2).

Enforces the contract stated in manifest.yaml:

- structural validity (required keys, enums, per-status mandatory fields),
- complete P01-P08 coverage with explicit status and reasons,
- bidirectional binding between ``implemented`` scenario entries and
  ``test_s<nn>_*`` test functions in this directory,
- the baseline-document sync obligation recorded in ``_meta``.

A drift in either direction (entry without test, test without entry, missing
coverage status) must fail here, not surface as a silently uncovered P-group.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

SCENARIO_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = SCENARIO_DIR / "manifest.yaml"
REPO_ROOT = SCENARIO_DIR.parent.parent

P_GROUP_IDS = {f"P{n:02d}" for n in range(1, 9)}
SCENARIO_STATUSES = {"implemented", "planned"}
P_GROUP_STATUSES = {"covered", "partial", "planned", "deferred"}
TEST_DEF_PATTERN = re.compile(r"def (test_s(\d+)[0-9a-z_]*)\s*\(")
SC_TEST_DEF_PATTERN = re.compile(r"def (test_sc(\d+)[0-9a-z_]*)\s*\(")
SC_RUN_MODES = {"standalone", "managed", "conditional"}
# When an SC scenario is still `planned`, its registration row in the
# standalone baseline doc (§5) must not claim delivered status.
SC_DELIVERED_MARKERS = ("已支持", "已通过")


def _load_manifest() -> dict:
    return yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))


def _test_scenario_prefixes() -> set[str]:
    """Scenario ids (lowercase, e.g. 's01') that have matching test functions."""
    prefixes: set[str] = set()
    for path in sorted(SCENARIO_DIR.glob("test_*.py")):
        for match in TEST_DEF_PATTERN.finditer(path.read_text(encoding="utf-8")):
            prefixes.add(f"s{match.group(2)}")
    return prefixes


def test_manifest_is_structurally_valid() -> None:
    manifest = _load_manifest()
    assert manifest["schema_version"] == 1
    meta = manifest["_meta"]
    assert meta["baseline_doc"], "_meta.baseline_doc must name the referencing baseline document"
    assert meta["baseline_doc_sync"] in {"pending", "synced"}

    assert set(manifest["tiers"]) == {"tier1", "tier2"}
    assert all(manifest["tiers"].values()), "tier descriptions must be non-empty"

    ids = [s["id"] for s in manifest["scenarios"]]
    assert len(ids) == len(set(ids)), "scenario ids must be unique"
    for scenario in manifest["scenarios"]:
        sid = scenario["id"]
        assert re.fullmatch(r"S\d+", sid), f"{sid}: id must look like S<nn>"
        assert scenario["status"] in SCENARIO_STATUSES, f"{sid}: unknown status"
        assert scenario["tier"] in (1, 2), f"{sid}: tier must be 1 or 2"
        assert scenario["entries"], f"{sid}: entries must be non-empty"
        assert scenario["p_groups"], f"{sid}: p_groups must be non-empty"
        assert scenario["assertions"].strip(), f"{sid}: assertions must be described"
        if scenario["status"] == "planned":
            assert scenario.get("blocked_by"), f"{sid}: planned scenarios must name their gating change"
        else:
            assert not scenario.get("blocked_by"), f"{sid}: implemented scenarios must not carry blocked_by"


def test_p_group_coverage_is_explicit() -> None:
    manifest = _load_manifest()
    p_groups = manifest["p_groups"]
    assert set(p_groups) == P_GROUP_IDS, "every P01-P08 group must appear with a coverage status"
    for pid, entry in p_groups.items():
        assert entry["status"] in P_GROUP_STATUSES, f"{pid}: unknown coverage status"
        assert entry["reason"].strip(), f"{pid}: coverage status requires a reason"
        if entry["status"] == "planned":
            assert entry.get("blocked_by"), f"{pid}: planned coverage must name its gating change"
    referenced = {pg for s in manifest["scenarios"] for pg in s["p_groups"]}
    unknown = referenced - set(p_groups)
    assert not unknown, f"scenarios reference unknown p_groups: {sorted(unknown)}"


def test_implemented_scenarios_have_tests_and_vice_versa() -> None:
    manifest = _load_manifest()
    implemented = {s["id"].lower() for s in manifest["scenarios"] if s["status"] == "implemented"}
    planned = {s["id"].lower() for s in manifest["scenarios"] if s["status"] == "planned"}
    found = _test_scenario_prefixes()

    missing_tests = implemented - found
    assert not missing_tests, f"implemented scenarios without test_s<nn>_* tests: {sorted(missing_tests)}"

    orphan_tests = found - implemented - planned
    assert not orphan_tests, f"tests without a manifest scenario entry: {sorted(orphan_tests)}"


def test_baseline_doc_sync_obligation() -> None:
    """When _meta marks the baseline doc synced, it must actually reference us."""
    manifest = _load_manifest()
    if manifest["_meta"]["baseline_doc_sync"] != "synced":
        return
    baseline = REPO_ROOT / manifest["_meta"]["baseline_doc"]
    assert baseline.exists(), f"synced baseline doc missing: {baseline}"
    assert "tests/scenarios/manifest.yaml" in baseline.read_text(encoding="utf-8"), (
        "synced baseline doc must reference tests/scenarios/manifest.yaml (reference, not copy)"
    )


def test_sc_group_is_structurally_valid() -> None:
    """SC (standalone-consumption) entries follow their own contract.

    They live under a dedicated ``sc_scenarios`` key so the S/P validation
    above keeps its original semantics. ``planned`` entries must name their
    responsible step and gating change; ``implemented`` entries must not
    carry ``blocked_by`` (same rule as S scenarios).
    """
    manifest = _load_manifest()
    sc_entries = manifest["sc_scenarios"]
    assert sc_entries, "sc_scenarios must exist and be non-empty"

    ids = [entry["id"] for entry in sc_entries]
    assert len(ids) == len(set(ids)), "SC ids must be unique"
    for entry in sc_entries:
        sid = entry["id"]
        assert re.fullmatch(r"SC\d+", sid), f"{sid}: id must look like SC<nn>"
        assert entry["status"] in SCENARIO_STATUSES, f"{sid}: unknown status"
        assert entry["run_mode"] in SC_RUN_MODES, f"{sid}: unknown run_mode"
        assert entry["tier"] in (1, 2), f"{sid}: tier must be 1 or 2"
        assert entry["title"].strip(), f"{sid}: title must be non-empty"
        assert entry["assertions"].strip(), f"{sid}: assertions must be described"
        if entry["status"] == "planned":
            assert entry.get("responsible_step"), f"{sid}: planned SC must name its responsible step"
            assert entry.get("blocked_by"), f"{sid}: planned SC must name its gating change"
        else:
            assert not entry.get("blocked_by"), f"{sid}: implemented SC must not carry blocked_by"


def test_sc_implemented_tests_binding() -> None:
    """Bidirectional binding between implemented SC entries and tests.

    Mirrors ``test_implemented_scenarios_have_tests_and_vice_versa`` for the
    ``test_sc<nn>_*`` prefix. Fixture-level helpers (``test_sc_fixture_*``)
    deliberately do not match either regex: they verify stub behavior, not a
    scenario delivery.
    """
    manifest = _load_manifest()
    implemented = {e["id"].lower() for e in manifest["sc_scenarios"] if e["status"] == "implemented"}
    found = {
        f"sc{match.group(2)}"
        for path in sorted(SCENARIO_DIR.glob("test_*.py"))
        for match in SC_TEST_DEF_PATTERN.finditer(path.read_text(encoding="utf-8"))
    }
    missing_tests = implemented - found
    assert not missing_tests, f"implemented SC scenarios without test_sc<nn>_* tests: {sorted(missing_tests)}"
    orphan_tests = found - implemented
    assert not orphan_tests, f"test_sc<nn>_* tests without an implemented SC entry: {sorted(orphan_tests)}"


def test_standalone_baseline_doc_registration_alignment() -> None:
    """The standalone baseline doc's §5 registration table tracks the SC group.

    - when ``standalone_baseline_doc_sync`` is ``synced``, the doc must exist
      and reference this manifest (reference, not copy);
    - every SC id in the manifest must have a registration row in the doc;
    - while an SC entry is ``planned``, its doc row must not claim delivered
      status (已支持/已通过).
    """
    manifest = _load_manifest()
    doc_path = manifest["_meta"].get("standalone_baseline_doc")
    assert doc_path, "_meta.standalone_baseline_doc must name the standalone baseline document"
    doc = REPO_ROOT / doc_path
    if manifest["_meta"]["standalone_baseline_doc_sync"] == "synced":
        assert doc.exists(), f"synced standalone baseline doc missing: {doc}"
        assert "tests/scenarios/manifest.yaml" in doc.read_text(encoding="utf-8"), (
            "synced standalone baseline doc must reference tests/scenarios/manifest.yaml"
        )
    if not doc.exists():
        return

    doc_text = doc.read_text(encoding="utf-8")
    rows = {match.group(1): match.group(0) for match in re.finditer(r"^\|\s*(SC\d+)\s*\|.*$", doc_text, re.MULTILINE)}
    for entry in manifest["sc_scenarios"]:
        sid = entry["id"]
        assert sid in rows, f"{sid}: no registration row in {doc_path} (§5 table)"
        if entry["status"] == "planned":
            assert not any(marker in rows[sid] for marker in SC_DELIVERED_MARKERS), (
                f"{sid}: planned SC row must not claim delivered status"
            )
