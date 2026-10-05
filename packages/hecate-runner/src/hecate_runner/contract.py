"""Execution-backend contract adapter for the standalone host.

The authoritative schemas remain in the platform contract tree. The wheel ships
a byte-for-byte synchronized snapshot so a standalone installation can validate
the public HTTP protocol without importing the full Hecate application.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema
from hecate_durable.contracts.events import (
    ActorKind,
    ActorRef,
    EventEnvelope,
    EventKind,
    EventSource,
)
from hecate_durable.contracts.references import BackendRef, RefKind
from referencing import Registry, Resource

CONTRACT_VERSION = "0.1"
CAPABILITIES_CONTRACT_VERSION = "0.2"
BACKEND_TYPE = "standalone-runner"
ISSUER_DOMAIN = "standalone-host"


class ContractValidationError(ValueError):
    """A request does not satisfy the published execution-backend schema."""


@lru_cache(maxsize=1)
def _schema_registry() -> Registry:
    bundled = Path(__file__).with_name("_contract_schemas")
    source_dir = Path(__file__).resolve().parents[4] / "src" / "hecate" / "contracts" / "schemas"
    schema_dir = bundled if bundled.exists() else source_dir
    resources: list[tuple[str, Resource]] = []
    for path in sorted(schema_dir.glob("*.schema.json")):
        contents = json.loads(path.read_text(encoding="utf-8"))
        resources.append((contents["$id"], Resource.from_contents(contents)))
    return Registry().with_resources(resources)


@lru_cache(maxsize=1)
def _execution_validator() -> jsonschema.Draft202012Validator:
    schema_dir = Path(__file__).with_name("_contract_schemas")
    if not schema_dir.exists():
        schema_dir = Path(__file__).resolve().parents[4] / "src" / "hecate" / "contracts" / "schemas"
    schema = json.loads((schema_dir / "execution-request.schema.json").read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(schema, registry=_schema_registry())


def validate_document(document: dict[str, Any], schema_name: str) -> None:
    """Validate a public response against the bundled authoritative schema."""

    version = CAPABILITIES_CONTRACT_VERSION if schema_name in {"capabilities", "errors"} else CONTRACT_VERSION
    target = {"$ref": f"https://hecate.dev/contracts/execution/{version}/{schema_name}.schema.json"}
    jsonschema.Draft202012Validator(target, registry=_schema_registry()).validate(document)


def bundled_schema_names() -> tuple[str, ...]:
    """Return the schema snapshot shipped with this host installation."""

    return tuple(sorted(path.name for path in Path(__file__).with_name("_contract_schemas").glob("*.schema.json")))


def is_execution_request(body: dict[str, Any]) -> bool:
    return isinstance(body, dict) and "contract_version" in body and "idempotency_key" in body and "task_ref" in body


def parse_execution_request(body: dict[str, Any], *, idempotency_header: str | None) -> dict[str, Any]:
    errors = sorted(_execution_validator().iter_errors(body), key=lambda error: list(error.path))
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.path) or "request"
        raise ContractValidationError(f"{location}: {first.message}")
    if body["contract_version"] != CONTRACT_VERSION:
        raise ContractValidationError(f"unsupported contract version {body['contract_version']!r}")
    if idempotency_header is None:
        raise ContractValidationError("Idempotency-Key header is required")
    if idempotency_header != body["idempotency_key"]:
        raise ContractValidationError("Idempotency-Key header must equal body idempotency_key")
    input_payload = body["input"]
    arguments = input_payload.get("tool_arguments", input_payload)
    return {"input": input_payload, "tool_arguments": arguments}


def backend_run_ref(run_id: str, run_ref: BackendRef | None = None) -> BackendRef:
    return run_ref or BackendRef(kind=RefKind.RUN, issuer_domain=ISSUER_DOMAIN, id=run_id)


def submit_receipt(run_id: str, state, run_ref: BackendRef | None = None) -> dict[str, Any]:
    receipt = {
        "run_ref": backend_run_ref(run_id, run_ref).to_dict(),
        "received_at": getattr(state, "received_at", None) or _now_iso(),
        "detail_ns": {"idempotency_key": getattr(state, "idempotency_key", None)},
    }
    return receipt


def run_status(run_id: str, state, run_ref: BackendRef | None = None) -> dict[str, Any]:
    status = state.status
    formal_state = {
        "queued": "pending",
        "waiting_input": "unknown",
        "waiting_approval": "unknown",
        "reconciliation_required": "unknown",
    }.get(status, status)
    if formal_state not in {"pending", "running", "succeeded", "failed", "cancelled", "unknown"}:
        formal_state = "unknown"
    detail: dict[str, Any] = {"task_state": status}
    if state.error:
        detail["error"] = state.error
    if state.result_ref:
        detail["result_ref"] = state.result_ref
    return {
        "run_ref": backend_run_ref(run_id, run_ref).to_dict(),
        "state": formal_state,
        "detail_ns": detail,
    }


def cancel_receipt(run_id: str, *, accepted: bool, state, run_ref: BackendRef | None = None) -> dict[str, Any]:
    receipt_state = "requested" if accepted else "rejected"
    detail = {"task_state": state.status}
    if not accepted:
        detail["reason"] = "run is not in a cancellable state"
    return {
        "run_ref": backend_run_ref(run_id, run_ref).to_dict(),
        "state": receipt_state,
        "detail_ns": detail,
    }


def event_page(state, *, cursor: str | None, durable_page=None) -> dict[str, Any]:
    if durable_page is not None:
        return {
            "events": [envelope.to_dict() for envelope in durable_page.events],
            "next_cursor": str(durable_page.next_cursor),
            "has_more": False,
        }
    try:
        start = max(int(cursor) if cursor is not None else 0, 0)
    except ValueError:
        raise ContractValidationError("cursor is not a valid opaque continuation") from None
    events = state.events[start:]
    envelopes = [
        _preview_envelope(state, source_sequence=start + index + 1, event=event) for index, event in enumerate(events)
    ]
    return {
        "events": [envelope.to_dict() for envelope in envelopes],
        "next_cursor": str(start + len(envelopes)),
        "has_more": False,
    }


def artifact_page(run_id: str, state, run_ref: BackendRef | None = None) -> dict[str, Any]:
    artifacts: list[dict[str, str]] = []
    if state.result_ref is not None:
        artifacts.append(
            BackendRef(
                kind=RefKind.ARTIFACT,
                issuer_domain=ISSUER_DOMAIN,
                id=f"{backend_run_ref(run_id, run_ref).id}:events",
            ).to_dict()
        )
    return {"artifacts": artifacts}


def capabilities(runner_capabilities: dict[str, str], *, durable: bool) -> dict[str, Any]:
    now = _now_iso()
    return {
        "contract_version": CAPABILITIES_CONTRACT_VERSION,
        "backend_type": BACKEND_TYPE,
        "ownership": {
            "harness": "enterprise",
            "environment": "enterprise",
            "tool_execution": "backend",
        },
        "capabilities": {
            "provide_input": "unsupported",
            "resolve_approval": "unsupported",
            "pause": "unsupported",
            "resume": "unsupported",
            "export_context": "unsupported",
            "cancel": "cooperative",
            "events_resume": "cooperative" if durable else "unsupported",
        },
        "verification": {
            "cancel": {
                "source": "packages/hecate-runner/tests/test_durable.py",
                "checked_at": now,
                "contract_version": CAPABILITIES_CONTRACT_VERSION,
                "backend_version": "0.1",
                "deployment_shape": "standalone-local",
            },
            **(
                {
                    "events_resume": {
                        "source": "packages/hecate-runner/tests/test_contract.py",
                        "checked_at": now,
                        "contract_version": CONTRACT_VERSION,
                        "backend_version": "0.1",
                        "deployment_shape": "standalone-durable-sqlite",
                    }
                }
                if durable
                else {}
            ),
        },
        "runner": runner_capabilities,
    }


def parse_run_path_segment(segment: str) -> tuple[str, str | None]:
    if "/" not in segment:
        return segment, None
    issuer_domain, run_id = segment.split("/", 1)
    return run_id, issuer_domain


def _preview_envelope(state, *, source_sequence: int, event: dict[str, Any]) -> EventEnvelope:
    now = _now_iso()
    run_ref = backend_run_ref(state.run_id, state.run_ref)
    task_ref = state.task_ref or BackendRef(
        kind=RefKind.TASK,
        issuer_domain=ISSUER_DOMAIN,
        id=f"task-{state.run_id}",
    )
    return EventEnvelope(
        contract_version=CONTRACT_VERSION,
        kind=EventKind.EVENT,
        event_id=f"execution-{run_ref.id}-{source_sequence}",
        task_ref=task_ref,
        run_ref=run_ref,
        source_sequence=source_sequence,
        occurred_at=now,
        received_at=now,
        payload_schema_ref="urn:hecate:runner:engine-event/0",
        payload=event,
        actor=ActorRef(kind=ActorKind.SERVICE, id="hecate-runner"),
        source=EventSource.EXECUTION_BACKEND,
    )


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(tz=UTC).isoformat()
