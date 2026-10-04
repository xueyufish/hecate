"""Drift pins between the durable contracts and their runtime twins.

The durable contract layer is the cross-process form of semantics the runtime
already enforces (``tool-recovery`` spec). Alignment is executable, not
documentary: any one-sided enum change - Python mapping, runtime enum, or
authoritative schema - fails here.
"""

from __future__ import annotations

import json
from pathlib import Path

from hecate.contracts.execution.durable import ActionLedgerState, ActionOutcome
from hecate.contracts.execution.tools import ToolSideEffectClass
from hecate.runtime.workers.tool_worker import ToolExecutionState

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "hecate"
    / "contracts"
    / "schemas"
    / "durable-action-ledger.schema.json"
)


def test_action_states_match_runtime_tool_execution_states() -> None:
    # The runtime enum folds the terminal outcomes (succeeded/failed) into
    # the same enum; the contract deliberately splits them into
    # ActionLedgerState (recovery window) and ActionOutcome (result status).
    # The pin: the four recovery values match the runtime's recovery subset.
    runtime_outcomes = {
        ToolExecutionState.SUCCEEDED.value,
        ToolExecutionState.FAILED.value,
    }
    runtime_recovery = {member.value for member in ToolExecutionState} - runtime_outcomes
    contract_values = {member.value for member in ActionLedgerState}
    assert contract_values == runtime_recovery, (
        "durable ActionLedgerState and runtime ToolExecutionState recovery "
        "subset drifted; update both sides in one change"
    )


def test_outcome_statuses_match_runtime_enum() -> None:
    runtime_values = {member.value for member in ToolExecutionState}
    assert {ActionOutcome.SUCCEEDED.value, ActionOutcome.FAILED.value} <= runtime_values, (
        "runtime no longer carries the outcome terminals the contract ActionOutcome mirrors"
    )


def test_action_state_schema_matches_python_enum() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    schema_values = set(schema["$defs"]["actionState"]["enum"])
    python_values = {member.value for member in ActionLedgerState}
    assert schema_values == python_values


def test_side_effect_schema_matches_python_enum() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    schema_values = set(schema["$defs"]["sideEffectClass"]["enum"])
    python_values = {member.value for member in ToolSideEffectClass}
    assert schema_values == python_values
