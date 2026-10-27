"""Shared versioned delivery acceptance for controller final answers.

Both hosts (the platform dispatcher and the Runner engine) consume this
module so one declaration and one delivery always classify identically.
The declaration digest freezes the complete standard — the version plus
the actual check parameters — so a standard modified under the same
version number can never silently re-evaluate decisions that were
committed under the original one: controllers pin the digest in every
decision's ``input_refs`` and a re-presented chain under a different
standard conflicts instead of replaying.

Delivery, execution and acceptance stay separate facts. An accepted final
answer is the delivered content; the acceptance record is the durable
evidence of which standard version and digest evaluated it, what each
required check returned and who is accountable for the verdict. A failed
or unverified required check withholds success but never erases the
delivered content or the confirmed business actions behind it. A run with
no acceptance declaration may still succeed on its final answer, but its
record says ``not_declared`` — it must never be read as a completed
business-goal acceptance.
"""

from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass, field
from typing import Any

from hecate_runtime.accepted_decision import canonical_json

__all__ = [
    "ACCEPTANCE_CHECKER_VERSION",
    "AcceptanceDeclaration",
    "declared_acceptance",
    "evaluate_delivery",
    "evaluation_declaration",
    "validate_acceptance_declaration",
    "validate_acceptance_record",
]

# Bumped whenever the evaluation semantics of any check kind change; the
# recorded value lets later audits re-interpret per-check verdicts under
# the rules that produced them.
ACCEPTANCE_CHECKER_VERSION = "delivery-acceptance/1"

SUPPORTED_CHECK_KINDS = ("output_schema", "content_contains")

_STATUS_PASSED = "passed"
_STATUS_FAILED = "acceptance_failed"
_STATUS_UNVERIFIED = "acceptance_unverified"
_STATUS_NOT_DECLARED = "not_declared"


@dataclass(frozen=True)
class AcceptanceDeclaration:
    """One frozen acceptance standard: a version plus its required checks.

    ``sha256`` digests the canonical form of version and all check
    parameters, so two declarations share a digest only when they are the
    same standard. Unknown check kinds are rejected here — an unsupported
    kind is an authoring error at declaration time, while evaluation-time
    unavailability (a check this checker cannot execute) is recorded as an
    ``unverified`` verdict that can never grant success.
    """

    version: int
    required_checks: tuple[dict[str, Any], ...] = ()
    sha256: str = field(default="", compare=False)

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version < 1:
            raise ValueError("acceptance.version must be a positive integer")
        checks = self.required_checks
        if not isinstance(checks, tuple):
            raise ValueError("acceptance.required_checks must be an array")
        for check in checks:
            if not isinstance(check, dict):
                raise ValueError("each acceptance required check must be an object")
            _validate_check_strict(check)
        _set_digest(self)

    @classmethod
    def persisted(cls, version: Any, checks: Any) -> AcceptanceDeclaration:
        """Lenient view over a persisted acceptance block (evaluation only).

        Admission validated the block when it was accepted; the checker
        version may have moved since, so this construction keeps unknown
        or malformed checks as-is — evaluation records them ``unverified``
        and they can never grant success. A version that is no longer a
        positive integer refuses evaluation (post-admission tampering).
        """

        if type(version) is not int or version < 1:
            raise ValueError("acceptance.version must be a positive integer")
        if not isinstance(checks, (tuple, list)):
            raise ValueError("acceptance.required_checks must be an array")
        normalized: tuple[Any, ...] = tuple(checks)
        declaration = object.__new__(cls)
        object.__setattr__(declaration, "version", version)
        object.__setattr__(declaration, "required_checks", normalized)
        _set_digest(declaration)
        return declaration

    def to_dict(self) -> dict[str, Any]:
        """Return a detached declaration after checking its frozen digest."""
        if self.sha256 != _declaration_digest(self):
            raise ValueError("acceptance declaration digest mismatch")
        return {
            "version": self.version,
            "required_checks": copy.deepcopy(list(self.required_checks)),
            "sha256": self.sha256,
        }


def _validate_check_strict(check: dict[str, Any]) -> None:
    kind = check.get("kind")
    if kind == "output_schema":
        schema = check.get("schema")
        if not isinstance(schema, dict):
            raise ValueError("output_schema check requires a schema object")
        _check_schema_offline(schema)
    elif kind == "content_contains":
        if not isinstance(check.get("value"), str) or not check["value"]:
            raise ValueError("content_contains check requires a nonempty value")
    else:
        raise ValueError(f"unsupported acceptance check kind {kind!r}")


def _set_digest(declaration: AcceptanceDeclaration) -> None:
    object.__setattr__(declaration, "required_checks", copy.deepcopy(declaration.required_checks))
    object.__setattr__(declaration, "sha256", _declaration_digest(declaration))


def _declaration_digest(declaration: AcceptanceDeclaration) -> str:
    return hashlib.sha256(
        canonical_json({"version": declaration.version, "required_checks": list(declaration.required_checks)}).encode(
            "utf-8"
        )
    ).hexdigest()


def validate_acceptance_declaration(acceptance: Any) -> None:
    """Validate a caller-supplied acceptance block; raise ``ValueError``.

    Fail fast on a malformed declaration at submission — before any model
    usage — so an invalid standard can never gate a delivery later.
    """

    if acceptance is None:
        return
    if not isinstance(acceptance, dict):
        raise ValueError("acceptance must be an object")
    checks = acceptance.get("required_checks", [])
    if not isinstance(checks, list):
        raise ValueError("acceptance.required_checks must be an array")
    AcceptanceDeclaration(version=acceptance.get("version", 1), required_checks=tuple(checks))


def declared_acceptance(acceptance: Any) -> AcceptanceDeclaration | None:
    """The acceptance block's declaration, or ``None`` when none was declared.

    This is the binding source for controller ``input_refs`` pinning: only
    an explicitly declared acceptance block pins ``acceptance_version`` and
    ``acceptance_digest`` on decisions. A bare ``output_schema`` keeps its
    historical evaluation (one version-1 schema check) without newly
    pinning acceptance references, so chains committed before this
    binding existed replay byte-for-byte. This reads an already admitted
    standard: unavailable check implementations do not prevent restoring
    its identity. Admission uses ``validate_acceptance_declaration``.
    """

    if acceptance is None:
        return None
    if not isinstance(acceptance, dict):
        raise ValueError("acceptance must be an object")
    checks = acceptance.get("required_checks", [])
    if not isinstance(checks, list):
        raise ValueError("acceptance.required_checks must be an array")
    return AcceptanceDeclaration.persisted(acceptance.get("version", 1), checks)


def evaluation_declaration(acceptance: Any, output_schema: Any) -> AcceptanceDeclaration | None:
    """The declaration a delivery is evaluated under.

    An explicit acceptance block wins. Without one, a bare
    ``output_schema`` evaluates as a single version-1 output-schema check
    (the historical behavior). Neither declared means ``None``: the
    delivery is recorded as ``not_declared``, not accepted. Construction
    is the lenient persisted view — admission already validated the block
    when the task was submitted, and a check this checker version cannot
    interpret evaluates ``unverified`` instead of crashing evaluation.
    """

    if acceptance is not None:
        if not isinstance(acceptance, dict):
            raise ValueError("acceptance must be an object")
        return AcceptanceDeclaration.persisted(acceptance.get("version", 1), acceptance.get("required_checks", []))
    if output_schema is not None:
        return AcceptanceDeclaration.persisted(1, ({"kind": "output_schema", "schema": output_schema},))
    return None


def evaluate_delivery(
    text: str,
    declaration: AcceptanceDeclaration | None,
    *,
    final_decision: dict[str, Any] | None = None,
    evaluator: dict[str, Any] | None = None,
    task_ref: dict[str, Any] | None = None,
    run_ref: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate one delivered final answer; return the acceptance record.

    The record binds the full namespace (task, run), the final decision's
    identity and digest, the standard's version and digest, the delivered
    content digest, the checker version and each check's verdict. It is
    deterministic in its inputs — the same declaration and the same
    delivered text always produce the same verdicts — so a redelivery
    after a crash re-derives, never re-asks, the model.
    """

    if not isinstance(text, str):
        raise ValueError("delivered content must be a string")
    if declaration is None:
        return _record(
            _STATUS_NOT_DECLARED,
            version=None,
            declaration_sha256=None,
            checks=[],
            text=text,
            final_decision=final_decision,
            evaluator=evaluator,
            task_ref=task_ref,
            run_ref=run_ref,
        )
    snapshot = declaration.to_dict()
    results: list[dict[str, Any]] = []
    status = _STATUS_PASSED
    problem: str | None = None
    for check in snapshot["required_checks"]:
        kind = str(check.get("kind") or "") if isinstance(check, dict) else ""
        unavailable = kind not in SUPPORTED_CHECK_KINDS
        try:
            if not isinstance(check, dict):
                raise ValueError("required check is malformed")
            _validate_check_strict(check)
            if kind == "output_schema":
                detail = _output_schema_problem(text, check["schema"])
            else:
                value = check["value"]
                detail = None if value in text else f"delivered content does not contain {value!r}"
        except Exception:
            unavailable = True
            detail = f"required check {kind!r} is not available" if kind else "required check is malformed"
        if detail is None:
            results.append({"kind": kind, "status": "passed"})
            continue
        failed_status = "unverified" if unavailable else "failed"
        results.append({"kind": kind, "status": failed_status, "detail": detail})
        if problem is None:
            status = _STATUS_UNVERIFIED if failed_status == "unverified" else _STATUS_FAILED
            problem = detail
    return _record(
        status,
        version=declaration.version,
        declaration_sha256=declaration.sha256,
        checks=results,
        text=text,
        final_decision=final_decision,
        evaluator=evaluator,
        task_ref=task_ref,
        run_ref=run_ref,
    )


def _record(
    status: str,
    *,
    version: int | None,
    declaration_sha256: str | None,
    checks: list[dict[str, Any]],
    text: str,
    final_decision: dict[str, Any] | None,
    evaluator: dict[str, Any] | None,
    task_ref: dict[str, Any] | None,
    run_ref: dict[str, Any] | None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "checker_version": ACCEPTANCE_CHECKER_VERSION,
        "version": version,
        "declaration_sha256": declaration_sha256,
        "status": status,
        "checks": checks,
        "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }
    if final_decision is not None:
        record["final_decision"] = dict(final_decision)
    if task_ref is not None:
        record["task_ref"] = dict(task_ref)
    if run_ref is not None:
        record["run_ref"] = dict(run_ref)
    if evaluator is not None:
        record["evaluated_by"] = dict(evaluator)
    record["record_sha256"] = hashlib.sha256(canonical_json(record).encode("utf-8")).hexdigest()
    return record


def validate_acceptance_record(record: dict[str, Any]) -> None:
    """Check retained evidence without rerunning historical checks on a read."""
    body = copy.deepcopy(record)
    digest = body.pop("record_sha256", None)
    if (
        record.get("checker_version") != ACCEPTANCE_CHECKER_VERSION
        or digest != hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()
    ):
        raise ValueError("stored acceptance record is corrupt or uses an unavailable checker version")


def _output_schema_problem(text: str, schema: Any) -> str | None:
    """First schema violation of the delivered text, or ``None`` when absent."""

    import json

    from jsonschema import Draft202012Validator

    if not isinstance(schema, dict):
        return "the declared output schema is unavailable"
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        return f"final answer is not valid JSON: {exc.msg}"
    try:
        _check_schema_offline(schema)
        violation = next(Draft202012Validator(schema).iter_errors(document), None)
    except Exception as exc:  # noqa: BLE001 — unresolvable references are contract failures
        return f"final answer violates the declared output schema: output schema cannot be evaluated: {exc}"
    return f"final answer violates the declared output schema: {violation.message}" if violation is not None else None


def _check_schema_offline(schema: dict[str, Any]) -> None:
    """Check schema syntax and prohibit network retrieval during validation."""

    from jsonschema import Draft202012Validator, SchemaError

    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ValueError(f"invalid output_schema: {exc.message}") from exc
    pending: list[Any] = [schema]
    single_schema = (
        "additionalProperties",
        "unevaluatedProperties",
        "propertyNames",
        "contains",
        "not",
        "if",
        "then",
        "else",
        "items",
        "additionalItems",
        "unevaluatedItems",
        "contentSchema",
    )
    schema_maps = ("$defs", "definitions", "properties", "patternProperties", "dependentSchemas", "dependencies")
    schema_arrays = ("allOf", "anyOf", "oneOf", "prefixItems")
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            for keyword in ("$ref", "$dynamicRef"):
                reference = item.get(keyword)
                if reference is not None and (not isinstance(reference, str) or not reference.startswith("#")):
                    raise ValueError("output_schema references must be local fragments")
            # Data in const/examples and property names are not schema keywords.
            pending.extend(item[key] for key in single_schema if key in item)
            for key in schema_maps:
                children = item.get(key)
                if isinstance(children, dict):
                    pending.extend(children.values())
            for key in schema_arrays:
                children = item.get(key)
                if isinstance(children, list):
                    pending.extend(children)
        elif isinstance(item, list):
            pending.extend(item)
