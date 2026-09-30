"""Reference-kind separation (task 2.2).

Platform-side kinds (task/run/deployment/authorization) and vendor-side kinds
(session/turn) are distinct discriminants: a reference of one kind MUST NOT be
passed where another kind is required - in the mapping, in request validation,
and in the schema (const-kind definitions).
"""

from __future__ import annotations

import pytest

from hecate.contracts.execution.references import (
    BackendRef,
    RefKind,
    require_kind,
    run_ref,
    session_ref,
    task_ref,
)
from hecate.contracts.execution.request import ExecutionRequest
from tests.test_execution.conftest import SAMPLES_DIR, load_sample


def test_factories_pin_the_kind() -> None:
    assert run_ref("platform", "r-1").kind is RefKind.RUN
    assert task_ref("platform", "t-1").kind is RefKind.TASK
    assert session_ref("vendor:openai", "s-1").kind is RefKind.SESSION


def test_require_kind_rejects_mismatch() -> None:
    run = run_ref("platform", "r-1")
    with pytest.raises(ValueError, match="kind mismatch"):
        require_kind(run, RefKind.SESSION)


def test_vendor_session_cannot_stand_in_for_platform_run() -> None:
    vendor_session = session_ref("vendor:openai", "sess_abc")
    with pytest.raises(ValueError, match="kind mismatch"):
        require_kind(vendor_session, RefKind.RUN)


def test_request_rejects_session_kind_in_run_slot() -> None:
    sample = load_sample(SAMPLES_DIR / "requests" / "submit-minimal.json")
    data = dict(sample)
    data["run_ref"] = {"kind": "session", "issuer_domain": "vendor:openai", "id": "sess_abc"}
    with pytest.raises(ValueError, match="kind mismatch"):
        ExecutionRequest.from_dict(data)


def test_base_ref_still_requires_known_kind() -> None:
    with pytest.raises(ValueError):
        BackendRef(kind="galaxy", issuer_domain="platform", id="x-1")  # type: ignore[arg-type]
