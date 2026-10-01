"""HTTP/JSON binding tests: structure, sample inter-checks, error mapping.

The binding file keeps a single source of truth by referencing the
authoritative schemas by ``$id``; structure is validated through the
test-side expansion (openapi_support). Positive samples validate their
request/response bodies against the governing authoritative schemas; negative
samples (caller-synthesized codes offered as backend error responses) must be
rejected by the binding rules.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import openapi_spec_validator
import pytest

from tests.test_execution.conftest import SAMPLES_DIR, load_sample, validate_against_schema
from tests.test_execution.openapi_support import BINDING_PATH, expand_for_validation, load_binding

HTTP_SAMPLES_DIR = SAMPLES_DIR / "http"

# The four returnable codes and their fixed HTTP statuses.
RETURNABLE_STATUS: dict[str, int] = {
    "unsupported": 501,
    "authorization_denied": 403,
    "budget_exhausted": 429,
    "version_conflict": 409,
}
# Codes that are caller-synthesized states and never backend HTTP errors.
CALLER_SYNTHESIZED = {"unreachable", "outcome_unknown"}
ERROR_URI_BASE = "https://hecate.dev/contracts/errors/"


class BindingViolationError(AssertionError):
    """A sample violates the HTTP binding rules."""


def http_samples() -> list[Path]:
    return sorted(HTTP_SAMPLES_DIR.glob("*.json"))


def validate_http_pair(doc: dict[str, Any]) -> None:
    """Validate one request/response pair sample against the binding rules."""

    method, path = doc["request"]["method"], doc["request"]["path"]
    status, body = doc["response"]["status"], doc["response"]["body"]

    schema = _governed_request_schema(method, path)
    if schema and doc["request"]["body"] is not None:
        validate_against_schema(doc["request"]["body"], schema)

    response_schema = _governed_response_schema(method, path, status)
    if response_schema:
        validate_against_schema(body, response_schema)

    if (
        isinstance(body, dict)
        and (400 <= status < 600)
        and body.get("type", "").startswith(
            ("https://hecate.dev/contracts/errors/", "https://hecate.dev/contracts/binding/")
        )
    ):
        _validate_problem(body, status)


def _validate_problem(body: dict[str, Any], status: int) -> None:
    for field in ("type", "title", "status"):
        if field not in body:
            raise BindingViolationError(f"problem body missing {field!r}")
    if body["status"] != status:
        raise BindingViolationError("problem status must match the HTTP status")

    code = body.get("code")
    if code is None:
        # Binding-level problem (pre-contract rejection): type must use the
        # binding URI space, never the contract error space.
        if body["type"].startswith(ERROR_URI_BASE):
            raise BindingViolationError("binding-level problem must not use the contract error URI space")
        return
    if code in CALLER_SYNTHESIZED:
        raise BindingViolationError(f"{code} is caller-synthesized and never a backend HTTP error response")
    if code not in RETURNABLE_STATUS:
        raise BindingViolationError(f"{code!r} is not a returnable contract error code")
    if status != RETURNABLE_STATUS[code]:
        raise BindingViolationError(f"{code} maps to {RETURNABLE_STATUS[code]}, body declares {status}")
    if body["type"] != ERROR_URI_BASE + code:
        raise BindingViolationError(f"problem type must be {ERROR_URI_BASE}{code}")


def _governed_request_schema(method: str, path: str) -> str | None:
    if method == "POST" and path == "/runs":
        return "execution-request"
    return None


def _governed_response_schema(method: str, path: str, status: int) -> str | None:
    if path == "/runs" and method == "POST" and status == 202:
        return "submit-receipt"
    is_run_status = (
        path.startswith("/runs/") and "/events" not in path and "/cancel" not in path and "/artifacts" not in path
    )
    if is_run_status and method == "GET" and status == 200:
        return "run-status"
    if ("/events?" in path or path.endswith("/events")) and method == "GET" and status == 200:
        return "event-page"
    if path.endswith("/cancel") and method == "POST" and status == 202:
        return "cancel-receipt"
    return None


def test_binding_structure_is_valid_openapi() -> None:
    expanded = expand_for_validation(load_binding())
    openapi_spec_validator.validate(expanded)


def test_binding_refs_point_at_existing_authoritative_schemas() -> None:
    """Every external $ref in the repo binding file names a schema on disk."""

    schemas_by_uri = {
        json.loads(p.read_text(encoding="utf-8"))["$id"]
        for p in (BINDING_PATH.parents[1] / "schemas").glob("*.schema.json")
    }
    text = BINDING_PATH.read_text(encoding="utf-8")
    refs = [line.split("$ref:", 1)[1].strip() for line in text.splitlines() if "$ref:" in line]
    external = [r for r in refs if r.startswith("https://hecate.dev/contracts/")]
    assert external, "binding must reference the authoritative schemas"
    for ref in external:
        base = ref.split("#", 1)[0]
        assert base in schemas_by_uri, f"binding references unknown schema: {base}"


@pytest.mark.parametrize(
    "path", [p for p in http_samples() if not load_sample(p).get("negative")], ids=lambda p: p.name
)
def test_positive_http_sample_pair_is_valid(path) -> None:
    validate_http_pair(load_sample(path))


@pytest.mark.parametrize("path", [p for p in http_samples() if load_sample(p).get("negative")], ids=lambda p: p.name)
def test_negative_http_sample_pair_is_rejected(path) -> None:
    with pytest.raises(BindingViolationError):
        validate_http_pair(load_sample(path))


def test_error_status_mapping_table() -> None:
    """The four returnable codes map to exactly the documented statuses."""

    assert RETURNABLE_STATUS == {
        "unsupported": 501,
        "authorization_denied": 403,
        "budget_exhausted": 429,
        "version_conflict": 409,
    }


def test_openapi_declares_only_mapped_error_statuses() -> None:
    """Problem responses in the binding carry only the four mapped statuses."""

    binding = load_binding()
    problem_responses = {
        name: list(body["content"].keys())
        for name, body in binding["components"]["responses"].items()
        if "application/problem+json" in body.get("content", {})
    }
    assert set(problem_responses) >= {
        "UnsupportedProblem",
        "AuthorizationDeniedProblem",
        "BudgetExhaustedProblem",
        "VersionConflictProblem",
    }


def test_stream_endpoint_is_marked_optional() -> None:
    binding = load_binding()
    stream = binding["paths"]["/runs/{run_ref}/events:stream"]["get"]
    assert stream["x-optional-capability"] == "events_stream"


def test_problem_bodies_use_problem_json_media_type() -> None:
    binding = load_binding()
    for name, body in binding["components"]["responses"].items():
        if "Problem" in name:
            assert "application/problem+json" in body["content"], name
