"""Regression checks for Step3 review findings, shared across wire/mapping."""

from __future__ import annotations

from dataclasses import replace

import jsonschema
import pytest

from hecate.contracts.execution.capabilities import BackendCapabilities, CapabilityLevel
from hecate.contracts.execution.references import BackendRef, RefKind, run_ref
from hecate.contracts.execution.sandbox import CommandRequest, CreateEnvironmentRequest, SandboxFileRef, SandboxState
from hecate.execution.backend import (
    CancelReceipt,
    EventPage,
    ExecutionBackendError,
    RunStatus,
    SubmitReceipt,
    UnsupportedCapabilityError,
)
from hecate.execution.sandbox import InMemorySandboxProvider, SandboxIdempotencyConflictError
from hecate.execution.stub import StubExecutionBackend
from tests.test_execution.conftest import SAMPLES_DIR, load_sample, validate_against_schema
from tests.test_execution.test_live_pilot import make_request


@pytest.mark.parametrize(
    "name", ["provide_input", "resolve_approval", "pause", "resume", "export_context", "cancel", "callback"]
)
def test_schema_and_mapping_both_require_capability_evidence(name: str) -> None:
    data = load_sample(SAMPLES_DIR / "capabilities" / "stub-capabilities.json")
    data["capabilities"][name] = "cooperative"
    data["verification"] = {}
    with pytest.raises(jsonschema.ValidationError):
        validate_against_schema(data, "capabilities")
    with pytest.raises(ValueError, match="verification entry"):
        BackendCapabilities.from_dict(data)


def test_control_evidence_scope_roundtrips() -> None:
    data = load_sample(SAMPLES_DIR / "capabilities" / "vendor-hosted.json")
    data["capabilities"]["cancel"] = "cooperative"
    data["verification"] = {
        "cancel": {
            "source": "test report",
            "checked_at": "2026-10-01T00:00:00Z",
            "contract_version": "0.1",
            "backend_version": "0.1.0",
            "deployment_shape": "isolated_loopback",
            "observation_source": "backend_reported",
            "evidence_ref": "local://evidence/1",
            "valid_until": "2026-10-02T00:00:00Z",
        }
    }
    validate_against_schema(data, "capabilities")
    parsed = BackendCapabilities.from_dict(data)
    assert parsed.to_dict() == data
    assert parsed.level_of("tool_proxy") is CapabilityLevel.UNSUPPORTED


def test_self_reported_capability_cannot_be_declared_enforced() -> None:
    data = load_sample(SAMPLES_DIR / "capabilities" / "vendor-hosted.json")
    data["capabilities"]["cancel"] = "enforced"
    data["verification"] = {"cancel": {"source": "vendor claim", "checked_at": "2026-10-01T00:00:00Z"}}
    with pytest.raises(jsonschema.ValidationError):
        validate_against_schema(data, "capabilities")
    with pytest.raises(ValueError, match="independently observed"):
        BackendCapabilities.from_dict(data)


@pytest.mark.parametrize(
    "mapping,body",
    [
        (SubmitReceipt, {"run_ref": run_ref("backend", "r").to_dict(), "received_at": "2026-10-01T00:00:00Z"}),
        (RunStatus, {"run_ref": run_ref("backend", "r").to_dict(), "state": "running"}),
        (CancelReceipt, {"run_ref": run_ref("backend", "r").to_dict(), "state": "requested"}),
        (EventPage, {"events": [], "has_more": False, "next_cursor": "tail-0"}),
    ],
)
def test_response_extensions_survive_mapping(mapping, body) -> None:
    body = {**body, "future_extension": {"value": 1}}
    assert mapping.from_dict(body).to_dict() == body


def test_stub_only_declares_implemented_interactions() -> None:
    backend = StubExecutionBackend()
    receipt = backend.submit(make_request(key="stub-declared"))
    assert backend.describe_capabilities().level_of("provide_input") is CapabilityLevel.UNSUPPORTED
    with pytest.raises(UnsupportedCapabilityError):
        backend.provide_input(receipt.run_ref, {"text": "hello"})
    with pytest.raises(UnsupportedCapabilityError):
        backend.resolve_approval(receipt.run_ref, BackendRef(RefKind.APPROVAL, "local", "a"), True)


def test_stub_resumes_after_empty_tail_without_replay() -> None:
    backend = StubExecutionBackend()
    receipt = backend.submit(make_request(key="stub-tail"))
    empty = backend.read_events(receipt.run_ref)
    assert empty.next_cursor is not None
    backend.append_event(receipt.run_ref.id, "first", {})
    page = backend.read_events(receipt.run_ref, empty.next_cursor)
    assert [e.event_id for e in page.events] == ["first"]
    assert backend.read_events(receipt.run_ref, page.next_cursor).events == ()
    backend.append_event(receipt.run_ref.id, "second", {})
    assert [e.event_id for e in backend.read_events(receipt.run_ref, page.next_cursor).events] == ["second"]


def test_backend_owned_ids_do_not_collide_with_caller_local_ids() -> None:
    backend = StubExecutionBackend()
    first = backend.submit(make_request(key="caller-1"))
    second = backend.submit(make_request(key="caller-2"))
    assert first.run_ref != second.run_ref
    with pytest.raises(ValueError, match="issuer mismatch"):
        backend.get_run(replace(first.run_ref, issuer_domain="another-backend"))


def test_mutating_submitted_request_cannot_rewrite_idempotency_history() -> None:
    backend = StubExecutionBackend()
    request = make_request(key="immutable-history")
    backend.submit(request)
    request.input["objective"] = "Changed after submit"
    with pytest.raises(ExecutionBackendError) as excinfo:
        backend.submit(request)
    assert excinfo.value.error.detail_ns["reason"] == "idempotency_key_content_mismatch"


def test_sandbox_rejects_key_reuse_with_different_content() -> None:
    provider = InMemorySandboxProvider()
    req = CreateEnvironmentRequest(idempotency_id="env", run_ref=run_ref("local", "r"))
    info = provider.create_environment(req)
    with pytest.raises(SandboxIdempotencyConflictError):
        provider.create_environment(replace(req, isolation_profile="changed"))
    cmd = CommandRequest(command_id="c", sandbox_id=info.sandbox_id, argv=("echo", "one"))
    provider.submit_command(cmd)
    with pytest.raises(SandboxIdempotencyConflictError):
        provider.submit_command(replace(cmd, argv=("echo", "two")))
    assert provider.command_execution_count("c") == 1


def test_sandbox_never_regresses_terminated_or_executes_on_missing_environment() -> None:
    provider = InMemorySandboxProvider()
    with pytest.raises(KeyError):
        provider.submit_command(CommandRequest(command_id="missing", sandbox_id="no-env", argv=("echo",)))
    info = provider.create_environment(CreateEnvironmentRequest(idempotency_id="env", run_ref=run_ref("local", "r")))
    provider.terminate(info.sandbox_id)
    provider.settle_termination(info.sandbox_id)
    assert provider.terminate(info.sandbox_id).state is SandboxState.TERMINATED
    with pytest.raises(ValueError, match="ready environment"):
        provider.submit_command(CommandRequest(command_id="late", sandbox_id=info.sandbox_id, argv=("echo",)))


@pytest.mark.parametrize("path", [r"..\escape", "C:/escape", "a/../b", "/escape"])
def test_sandbox_paths_fail_schema_and_mapping(path: str) -> None:
    with pytest.raises(ValueError, match="sandbox-relative"):
        SandboxFileRef(sandbox_id="s", path=path)
    with pytest.raises(jsonschema.ValidationError):
        validate_against_schema({"sandbox_id": "s", "path": path}, "sandbox", "#/$defs/sandboxFileRef")
