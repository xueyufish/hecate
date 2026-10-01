"""Tool-contract tests: vocabulary alignment and failure-layer semantics.

The contract's side-effect vocabulary is adopted from the internal
``SideEffectClass`` enum; the consistency test pins the two enums together so
neither side can silently diverge. Failure layering: parameter validation
fails before dispatch, business rejection is a tool RESULT (run continues),
system failure is a tool-level error event, remote outcome unknown
reconciles by idempotent id.
"""

from __future__ import annotations

import pytest

from hecate.contracts.execution.tools import ToolDeclaration, ToolSideEffectClass
from hecate.runtime.tool_side_effects import SideEffectClass
from tests.test_execution.conftest import SAMPLES_DIR, load_sample, validate_against_schema

TOOLS_DIR = SAMPLES_DIR / "tools"
EVENTS_DIR = SAMPLES_DIR / "events"


def test_side_effect_vocabulary_matches_internal_enum() -> None:
    """Contract enum and internal SideEffectClass stay identical."""

    contract_values = {member.value for member in ToolSideEffectClass}
    internal_values = {member.value for member in SideEffectClass}
    assert contract_values == internal_values
    assert len(contract_values) == 5


def test_tool_declaration_samples_round_trip() -> None:
    for path in sorted(TOOLS_DIR.glob("*.json")):
        data = load_sample(path)
        declaration = ToolDeclaration.from_dict(data)
        assert declaration.to_dict() == data


def test_invalid_side_effect_class_is_rejected() -> None:
    data = load_sample(TOOLS_DIR / "readonly-lookup.json")
    with pytest.raises((ValueError, KeyError)):
        ToolDeclaration.from_dict({**data, "side_effect_class": "write_once_forgotten"})


def test_business_rejection_is_a_tool_result_event_not_an_execution_error() -> None:
    """Business rejection payload rides a normal event; no execution-failure marker."""

    data = load_sample(EVENTS_DIR / "tool-result-business-rejection.json")
    validate_against_schema(data, "event-envelope")
    assert data["payload"]["outcome"] == "business_rejected"
    # The envelope itself is kind=event (a result), not an execution failure.
    assert data["kind"] == "event"


def test_system_failure_is_a_tool_level_error_event() -> None:
    data = load_sample(EVENTS_DIR / "tool-error-system-failure.json")
    validate_against_schema(data, "event-envelope")
    assert data["payload"]["outcome"] == "system_failure"
    assert data["payload"]["tool"] == "create_ticket"


def test_remote_unknown_reconciles_by_idempotent_id() -> None:
    data = load_sample(EVENTS_DIR / "tool-outcome-unknown.json")
    validate_against_schema(data, "event-envelope")
    assert data["payload"]["outcome"] == "outcome_unknown"
    strategy = data["payload"]["reconciliation"]["strategy"]
    assert strategy in {"query_by_idempotency_key", "query_by_run_ref", "query_by_vendor_session"}


def test_tool_selection_is_namespaced_observable_fact() -> None:
    """Candidates/selection are carried as diagnostic facts, never reasoning."""

    data = load_sample(EVENTS_DIR / "tool-selection-observable.json")
    validate_against_schema(data, "event-envelope")
    assert data["tool_selection"]["selected"] == "inventory_lookup"
    assert isinstance(data["tool_selection"]["candidates"], list)
    assert "reasoning" not in data["tool_selection"]
