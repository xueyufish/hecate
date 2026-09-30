"""SandboxProvider contract tests (tasks 5.1 / 5.2).

Invariants under test: idempotent environment creation, idempotent command
submission (no second side effect), command timeout expressed as unknown and
reconciled by command ID, termination request separated from actual teardown,
explicit unsupported for undeclared optional abilities, and a version
namespace independent from the execution contract.
"""

from __future__ import annotations

from typing import Any

import pytest

from hecate.contracts.execution.capabilities import CapabilityLevel
from hecate.contracts.execution.references import run_ref
from hecate.contracts.execution.sandbox import (
    CommandRequest,
    CommandState,
    CreateEnvironmentRequest,
    SandboxFileRef,
    SandboxState,
)
from hecate.execution.sandbox import InMemorySandboxProvider, SandboxProvider, UnsupportedCapabilityError


@pytest.fixture()
def run() -> Any:
    return run_ref("platform", "r-456")


@pytest.fixture()
def provider(run: Any) -> InMemorySandboxProvider:
    provider = InMemorySandboxProvider()
    provider.create_environment(CreateEnvironmentRequest(idempotency_id="idem-sbx-1", run_ref=run))
    return provider


def test_provider_is_second_implementation_of_the_extension_point() -> None:
    assert issubclass(InMemorySandboxProvider, SandboxProvider)


def test_create_environment_is_idempotent(run: Any) -> None:
    provider = InMemorySandboxProvider()
    request = CreateEnvironmentRequest(idempotency_id="idem-sbx-1", run_ref=run)

    first = provider.create_environment(request)
    second = provider.create_environment(request)

    assert first.sandbox_id == second.sandbox_id
    assert provider.environment_count() == 1
    assert first.state is SandboxState.READY


def test_command_submission_is_idempotent(provider: InMemorySandboxProvider) -> None:
    request = CommandRequest(command_id="cmd-1", sandbox_id="sbx-1", argv=["echo", "hello"])

    first = provider.submit_command(request)
    replay = provider.submit_command(request)

    assert replay.command_id == first.command_id
    assert replay.state is first.state
    assert provider.command_execution_count("cmd-1") == 1


def test_command_timeout_is_unknown_and_reconciles_by_id(provider: InMemorySandboxProvider) -> None:
    provider.command_mode = "timeout"
    record = provider.submit_command(
        CommandRequest(command_id="cmd-timeout", sandbox_id="sbx-1", argv=["sleep", "999"])
    )

    assert record.state is CommandState.UNKNOWN
    assert record.exit_code is None

    # Reconcile by command ID: the true result is queried, never re-run.
    provider.settle_command("cmd-timeout", exit_code=0)
    reconciled = provider.get_command("cmd-timeout")
    assert reconciled.state is CommandState.COMPLETED
    assert reconciled.exit_code == 0
    assert provider.command_execution_count("cmd-timeout") == 1


def test_termination_request_is_not_destroyed(provider: InMemorySandboxProvider) -> None:
    info = provider.terminate("sbx-1")
    assert info.state is SandboxState.TERMINATING

    observed = provider.get_environment("sbx-1")
    assert observed.state is SandboxState.TERMINATING

    provider.settle_termination("sbx-1")
    assert provider.get_environment("sbx-1").state is SandboxState.TERMINATED


def test_undeclared_ability_is_explicitly_unsupported(provider: InMemorySandboxProvider) -> None:
    caps = provider.describe_capabilities()
    assert caps.level_of("snapshot") is CapabilityLevel.UNSUPPORTED

    with pytest.raises(UnsupportedCapabilityError) as excinfo:
        provider.snapshot("sbx-1")
    assert excinfo.value.code == "unsupported"
    assert excinfo.value.ability == "snapshot"


def test_renew_lease_extends_validity(provider: InMemorySandboxProvider) -> None:
    before = provider.get_environment("sbx-1").leases_until
    after = provider.renew_lease("sbx-1", seconds=3600)
    assert after.leases_until is not None
    assert after.leases_until != before


def test_file_transfer_uses_sandbox_relative_references(
    provider: InMemorySandboxProvider,
) -> None:
    ref = SandboxFileRef(sandbox_id="sbx-1", path="work/out.txt")
    provider.upload_file(ref, b"artifact-bytes")
    assert provider.download_file(ref) == b"artifact-bytes"

    with pytest.raises(ValueError, match="sandbox-relative"):
        SandboxFileRef(sandbox_id="sbx-1", path="/host/etc/passwd")


def test_sandbox_contract_is_versioned_independently() -> None:
    from tests.test_execution.conftest import load_registry, schema_uri

    registry = load_registry()
    sandbox_uri = schema_uri("sandbox")
    execution_uri = schema_uri("execution-request")
    assert "/sandbox/0.1/" in sandbox_uri
    assert "/execution/0.1/" in execution_uri
    # Both are resolvable in the same registry yet carry independent namespaces.
    assert sandbox_uri != execution_uri
    assert registry[sandbox_uri] is not None
