"""Whitelisted run-level view of the shared delivery-acceptance record.

The authoritative acceptance record is produced once, by the shared runtime
evaluation (``hecate_runtime.delivery_acceptance``), and committed with the
durable terminal state. This module never re-evaluates, re-derives or
re-interprets a verdict. Two writers copy the same recorded fact into the
run projection — the dispatcher's direct projection and the outbox relay's
recovery projection — so a terminal commit, a post-commit crash and a
replay all land on identical run-level facts.

Reading is passive but verified. The stored record is checked with the
shared integrity validation (record digest and checker version) before its
whitelisted form reaches a Run query; a record this build cannot verify
never displays as a pass — it is reported as ``uninterpretable``. A run
without a stored record has no acceptance record at all (an execution that
predates the surface or never evaluated a delivery), which is distinct
from ``not_declared`` — itself a recorded verdict.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

__all__ = [
    "ACCEPTANCE_VIEW_STATUSES",
    "UNINTERPRETABLE",
    "acceptance_record_of",
    "read_acceptance_view",
    "delivery_projection",
]

ACCEPTANCE_VIEW_STATUSES = (
    "passed",
    "acceptance_failed",
    "acceptance_unverified",
    "not_declared",
)
UNINTERPRETABLE = "uninterpretable"

_UNVERIFIABLE_REASON = "stored acceptance record is malformed, mismatched or uses an unavailable checker version"

# Field-by-field copy on purpose: the durable record's full body (task and
# run references, future fields) stays out of the query response, and future
# record additions never leak into Run queries by default.
_VIEW_FIELDS = (
    "status",
    "version",
    "declaration_sha256",
    "checker_version",
    "content_sha256",
    "record_sha256",
)


def acceptance_record_of(outcome: Mapping[str, Any]) -> Any:
    """The acceptance fact a terminal outcome carries, or ``None``.

    A detached deep copy for the run projection: the projection stores the
    recorded record verbatim (like the durable task result already does);
    verification and whitelisting happen on read, where the full record is
    still available for the digest check.
    """

    return deepcopy(outcome.get("acceptance"))


def delivery_projection(outcome: Mapping[str, Any], previous: Mapping[str, Any]) -> dict[str, Any]:
    """Copy delivery facts identically on direct completion and outbox replay."""
    content = str(outcome.get("content") or "")
    truncated = len(content) > 2000 or outcome.get("result_preview_truncated") is True
    acceptance = acceptance_record_of(outcome)
    if acceptance is None:
        acceptance = deepcopy(previous.get("delivery_acceptance"))
        # A backfill carries only the old preview, not the full delivery.
        # A missing delivery likewise cannot erase that recorded content.
        if acceptance is not None and (outcome.get("content") is None or content == previous.get("result_preview")):
            content = str(previous.get("result_preview") or "")
            truncated = previous.get("result_preview_truncated") is True
    projection: dict[str, Any] = {"result_preview": content[:2000]}
    if truncated:
        projection["result_preview_truncated"] = True
    if acceptance is not None:
        projection["delivery_acceptance"] = acceptance
    return projection


def read_acceptance_view(
    stored: Any,
    *,
    task_ref: dict[str, str] | None = None,
    run_ref: dict[str, str] | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any] | None:
    """Verify and whitelist a stored record for a Run query response.

    ``None`` (nothing stored) stays ``None``: absence is the honest "no
    record", never a verdict. A stored record that fails the shared
    integrity validation — digest tampering, or a checker version this
    build does not ship — becomes an ``uninterpretable`` view instead of
    reaching a client as a pass. No check is re-run here.
    """

    if stored is None:
        return None
    if not isinstance(stored, dict):
        return _uninterpretable(stored)
    try:
        from hecate_runtime.delivery_acceptance import validate_acceptance_record

        validate_acceptance_record(stored)
        _validate_shape(stored)
        if task_ref is not None and stored.get("task_ref") != task_ref:
            raise ValueError("acceptance belongs to another task")
        if run_ref is not None and stored.get("run_ref") != run_ref:
            raise ValueError("acceptance belongs to another run")
        evaluator = stored.get("evaluated_by") or {}
        if workspace_id is not None and evaluator.get("workspace_id", workspace_id) != workspace_id:
            raise ValueError("acceptance belongs to another workspace")
    except (ValueError, TypeError):
        return _uninterpretable(stored)
    view: dict[str, Any] = {}
    for field in _VIEW_FIELDS:
        value = stored.get(field)
        if value is not None:
            view[field] = value
    checks = stored.get("checks")
    if isinstance(checks, list):
        view["checks"] = [_check_view(check) for check in checks]
    final_decision = stored.get("final_decision")
    if isinstance(final_decision, dict):
        view["final_decision"] = _string_fields(final_decision, ("decision_step_id", "content_digest"))
    evaluated_by = stored.get("evaluated_by")
    if isinstance(evaluated_by, dict):
        view["evaluated_by"] = _string_fields(evaluated_by, ("host", "workspace_id", "submitted_by", "principal"))
    if view.get("status") not in ACCEPTANCE_VIEW_STATUSES:
        return _uninterpretable(stored)
    return view


def _check_view(check: Any) -> dict[str, Any]:
    """Keep verdicts while excluding schema values and exception diagnostics."""

    if not isinstance(check, dict):
        return {"kind": None, "status": None, "detail": "stored check result is malformed"}
    view: dict[str, Any] = {"kind": check.get("kind"), "status": check.get("status")}
    if check.get("status") == "failed":
        view["detail"] = "Required check failed."
    elif check.get("status") == "unverified":
        view["detail"] = "Required check could not be verified."
    return view


def _string_fields(value: dict[str, Any], fields: tuple[str, ...]) -> dict[str, str]:
    return {field: value[field] for field in fields if isinstance(value.get(field), str)}


def _validate_shape(record: dict[str, Any]) -> None:
    status = record.get("status")
    if not isinstance(status, str) or status not in ACCEPTANCE_VIEW_STATUSES:
        raise ValueError("unknown acceptance status")
    for key in ("content_sha256", "record_sha256"):
        _require_digest(record.get(key))
    if status == "not_declared":
        if record.get("version") is not None or record.get("declaration_sha256") is not None:
            raise ValueError("undeclared acceptance has a standard")
    else:
        if type(record.get("version")) is not int or record["version"] < 1:
            raise ValueError("invalid acceptance version")
        _require_digest(record.get("declaration_sha256"))
    checks = record.get("checks")
    if not isinstance(checks, list):
        raise ValueError("invalid acceptance checks")
    for check in checks:
        if (
            not isinstance(check, dict)
            or not isinstance(check.get("kind"), str)
            or not isinstance(check.get("status"), str)
            or check["status"] not in ("passed", "failed", "unverified")
        ):
            raise ValueError("invalid check result")
    first_failure = next((check["status"] for check in checks if check["status"] != "passed"), None)
    expected = {None: "passed", "failed": "acceptance_failed", "unverified": "acceptance_unverified"}[first_failure]
    if (status == "not_declared" and checks) or (status != "not_declared" and status != expected):
        raise ValueError("acceptance status contradicts checks")
    for field in ("final_decision", "evaluated_by"):
        if field in record and not isinstance(record[field], dict):
            raise ValueError("invalid acceptance metadata")
    decision = record.get("final_decision")
    if decision is not None:
        if not isinstance(decision.get("decision_step_id"), str):
            raise ValueError("invalid final decision identity")
        _require_digest(decision.get("content_digest"))


def _require_digest(value: Any) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("invalid acceptance digest")


def _uninterpretable(record: Any) -> dict[str, Any]:
    view: dict[str, Any] = {"status": UNINTERPRETABLE, "reason": _UNVERIFIABLE_REASON}
    raw_status = record.get("status") if isinstance(record, dict) else None
    if isinstance(raw_status, str):
        view["recorded_status"] = raw_status
    checker = record.get("checker_version") if isinstance(record, dict) else None
    if isinstance(checker, str):
        view["checker_version"] = checker
    return view
