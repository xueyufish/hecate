"""Three-way consistency, part 2: the Python mapping parses every sample and
round-trips it without losing or renaming fields (tasks 1.3 / 2.1).

Removing any required field from a sample must break BOTH the schema check and
the mapping parse - that coupling is what keeps the hand-written schema files,
the hand-written mapping, and the hand-written samples from drifting apart.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from hecate.contracts.execution.capabilities import BackendCapabilities
from hecate.contracts.execution.errors import BackendError
from hecate.contracts.execution.events import EventEnvelope
from hecate.contracts.execution.manifest import ArtifactManifest
from hecate.contracts.execution.references import BackendRef
from hecate.contracts.execution.request import ExecutionRequest
from hecate.contracts.execution.sandbox import (
    CommandRecord,
    CreateEnvironmentRequest,
    SandboxInfo,
)
from hecate.contracts.execution.security import SecurityClaims
from hecate.contracts.execution.tools import ToolDeclaration
from tests.test_execution.conftest import (
    SAMPLES_DIR,
    load_sample,
    validate_against_schema,
)

Mapping = Callable[[dict[str, Any]], Any]


def _mapping_for(path: Path) -> Callable[[dict[str, Any]], Any] | None:
    theme = path.parent.name
    name = path.name
    if theme == "references":
        return BackendRef.from_dict
    if theme == "requests":
        return ExecutionRequest.from_dict
    if theme == "events":
        return EventEnvelope.from_dict
    if theme == "builtin":
        from hecate.execution.backend import CancelReceipt, RunStatus, SubmitReceipt

        if "submit-receipt" in name:
            return SubmitReceipt.from_dict
        if "run-status" in name:
            return RunStatus.from_dict
        if "cancel-receipt" in name:
            return CancelReceipt.from_dict
        return EventEnvelope.from_dict
    if theme == "errors":
        return BackendError.from_dict
    if theme == "capabilities":
        return BackendCapabilities.from_dict
    if theme == "manifest":
        return ArtifactManifest.from_dict
    if theme == "sandbox":
        if "create-environment" in name:
            return CreateEnvironmentRequest.from_dict
        if "sandbox-info" in name:
            return SandboxInfo.from_dict
        return CommandRecord.from_dict
    if theme == "security-claims":
        return SecurityClaims.from_dict
    if theme == "tools":
        return ToolDeclaration.from_dict
    return None


def _roundtrip(mapping: Mapping, sample: dict[str, Any]) -> dict[str, Any]:
    return mapping(sample).to_dict()


def _mappable_samples() -> list[Path]:
    """Samples with a registered mapping: excludes HTTP pairs (binding tests)
    and ``negative-*`` samples (asserted to be rejected, not parsed)."""

    return sorted(
        p for p in SAMPLES_DIR.rglob("*.json") if p.parent.name != "http" and not p.name.startswith("negative-")
    )


@pytest.mark.parametrize(
    "path",
    _mappable_samples(),
    ids=lambda p: f"{p.parent.name}/{p.name}",
)
def test_mapping_parses_sample_and_roundtrips(path) -> None:
    mapping = _mapping_for(path)
    assert mapping is not None, f"no mapping registered for {path}"
    sample = load_sample(path)
    assert _roundtrip(mapping, sample) == sample


@pytest.mark.parametrize(
    "path",
    _mappable_samples(),
    ids=lambda p: f"{p.parent.name}/{p.name}",
)
def test_removing_required_field_breaks_mapping_and_schema(path) -> None:
    mapping = _mapping_for(path)
    assert mapping is not None
    sample = load_sample(path)
    required_field = _required_field_for(path)
    broken = {key: value for key, value in sample.items() if key != required_field}
    assert broken != sample

    with pytest.raises((KeyError, ValueError)):
        mapping(broken)

    schema_name, fragment = _schema_for(path)
    with pytest.raises(Exception):  # noqa: B017 - jsonschema.ValidationError expected
        validate_against_schema(broken, schema_name, fragment)


def _required_field_for(path: Path) -> str:
    theme = path.parent.name
    name = path.name
    if theme == "references":
        return "kind"
    if theme == "requests":
        return "idempotency_key"
    if theme == "events":
        return "event_id"
    if theme == "builtin":
        if "submit-receipt" in name:
            return "run_ref"
        if "event-envelope" in name:
            return "event_id"
        return "state"
    if theme == "errors":
        return "code"
    if theme == "capabilities":
        return "ownership"
    if theme == "manifest":
        return "entry"
    if theme == "security-claims":
        return "aud"
    if theme == "tools":
        return "side_effect_class"
    if "create-environment" in name:
        return "idempotency_id"
    if "sandbox-info" in name:
        return "state"
    return "state"


def _schema_for(path: Path) -> tuple[str, str]:
    from tests.test_execution.conftest import sample_schema_ref

    return sample_schema_ref(path)


def test_unknown_request_fields_survive_roundtrip() -> None:
    """Forward compatibility: unknown fields are preserved, never dropped."""

    sample = load_sample(SAMPLES_DIR / "requests" / "submit-with-namespace.json")
    assert "x_vendor_hint" in sample  # the tolerance sample carries an unknown field

    request = ExecutionRequest.from_dict(sample)
    rebuilt = json.loads(json.dumps(request.to_dict()))
    assert rebuilt == sample
