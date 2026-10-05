"""Step3 execution-backend contract binding for the standalone host."""

from __future__ import annotations

import time
import urllib.parse
from pathlib import Path

import pytest
from conftest_runner import write_profile
from hecate_runner.contract import validate_document
from test_durable import _Harness, _wait_task_terminal, _write_request, _WriteAwareBusinessApi


@pytest.fixture()
def contract_database_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'contract-state.db'}"


@pytest.fixture()
def contract_profile(tmp_path: Path, contract_database_url: str, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("RUNNER_SHUTDOWN_TOKEN", "shutdown-token-value")
    return write_profile(
        tmp_path,
        allowlist=["query_inventory", "submit_inventory_update"],
        durable={"database_url": contract_database_url, "workspace": "standalone"},
        with_write_tool=True,
    )


@pytest.fixture()
def contract_harness(contract_profile: Path, contract_database_url: str):
    api = _WriteAwareBusinessApi()
    harness = _Harness(contract_profile, api.url, database_url=contract_database_url)
    yield harness
    harness.close(abandon=True)
    api.close()


def _execution_request(*, idempotency_key: str = "contract-key-1") -> dict:
    preview = _write_request()
    return {
        "contract_version": "0.1",
        "task_ref": {"kind": "task", "issuer_domain": "hecate-platform", "id": "platform-task-1"},
        "run_ref": {"kind": "run", "issuer_domain": "hecate-platform", "id": "platform-run-1"},
        "deployment_ref": {
            "kind": "deployment",
            "issuer_domain": "hecate-platform",
            "id": "platform-deployment-1",
        },
        "authorization_ref": {
            "kind": "authorization",
            "issuer_domain": "hecate-platform",
            "id": "platform-authorization-1",
        },
        "input": {
            "prompt": preview["input"]["prompt"],
            "tool_arguments": preview["tool_arguments"],
        },
        "idempotency_key": idempotency_key,
        "trace_correlation": {"trace_id": "contract-trace-1"},
    }


def _wire_run(receipt: dict) -> str:
    ref = receipt["run_ref"]
    return urllib.parse.quote(f"{ref['issuer_domain']}/{ref['id']}", safe="")


def _wait_formal_terminal(harness: _Harness, receipt: dict, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _, status = harness.call("GET", f"/runs/{_wire_run(receipt)}")
        if status["state"] in {"succeeded", "failed", "cancelled", "unknown"}:
            return status
        time.sleep(0.05)
    raise AssertionError("formal run did not reach a terminal state")


def test_public_contract_documents_validate_and_run(contract_harness: _Harness) -> None:
    _, capabilities = contract_harness.call("GET", "/capabilities", token=None)
    validate_document(capabilities, "capabilities")
    assert capabilities["backend_type"] == "standalone-runner"
    assert capabilities["capabilities"]["cancel"] == "cooperative"

    status_code, receipt = contract_harness.call(
        "POST",
        "/runs",
        _execution_request(),
        headers={"Idempotency-Key": "contract-key-1"},
    )
    assert status_code == 202
    validate_document(receipt, "submit-receipt")

    status = _wait_formal_terminal(contract_harness, receipt)
    validate_document(status, "run-status")
    assert status["state"] == "succeeded"

    _, events = contract_harness.call("GET", f"/runs/{_wire_run(receipt)}/events")
    validate_document(events, "event-page")
    assert events["events"]
    assert all(event["source"] == "execution_backend" for event in events["events"])
    _, artifacts = contract_harness.call("GET", f"/runs/{_wire_run(receipt)}/artifacts")
    assert all(ref["kind"] == "artifact" for ref in artifacts["artifacts"])


def test_idempotent_contract_replay_keeps_original_receipt(contract_harness: _Harness) -> None:
    request = _execution_request()
    _, first = contract_harness.call(
        "POST",
        "/runs",
        request,
        headers={"Idempotency-Key": request["idempotency_key"]},
    )
    _wait_formal_terminal(contract_harness, first)
    _, second = contract_harness.call(
        "POST",
        "/runs",
        request,
        headers={"Idempotency-Key": request["idempotency_key"]},
    )
    assert second == first
    tasks = contract_harness.call("GET", "/tasks")[1]["tasks"]
    assert len([task for task in tasks if task["run_ref"] == f"runs/{first['run_ref']['id']}"]) == 1

    request["input"]["prompt"] = "different semantic request"
    status, conflict = contract_harness.call(
        "POST",
        "/runs",
        request,
        headers={"Idempotency-Key": "contract-key-1"},
    )
    assert status == 409
    assert conflict["type"] == "https://hecate.dev/contracts/errors/version_conflict"
    validate_document(conflict, "errors")


def test_contract_status_and_events_survive_restart(
    contract_harness: _Harness,
    contract_profile: Path,
    contract_database_url: str,
) -> None:
    _, receipt = contract_harness.call(
        "POST",
        "/runs",
        _execution_request(idempotency_key="contract-key-restart"),
        headers={"Idempotency-Key": "contract-key-restart"},
    )
    _wait_formal_terminal(contract_harness, receipt)
    run_id = receipt["run_ref"]["id"]
    task_id = _wait_task_terminal(contract_harness, run_id)["task_ref"]
    del task_id
    contract_harness.close(abandon=True)

    api = _WriteAwareBusinessApi()
    try:
        restarted = _Harness(contract_profile, api.url, database_url=contract_database_url)
        try:
            _, status = restarted.call("GET", f"/runs/{_wire_run(receipt)}")
            validate_document(status, "run-status")
            assert status["state"] == "succeeded"

            _, events = restarted.call("GET", f"/runs/{_wire_run(receipt)}/events")
            validate_document(events, "event-page")
            assert events["events"]
        finally:
            restarted.close(abandon=True)
    finally:
        api.close()
