"""SC01: clean-install cold start with no control plane.

Gates: clean venv install of only the two wheels (no repo source path, no
editable install, no full-hecate application), no control-plane address in
the profile, cold start to a served HTTP surface, a read-only run
completing with a result reference, local evidence recording the run, and
capability declarations that state what the preview does not provide.
"""

from __future__ import annotations

import subprocess
import urllib.parse

import pytest

from tests.scenarios.tools import runner_harness


@pytest.fixture(scope="module")
def runner_stack(tmp_path_factory: pytest.TempPathFactory):
    if not runner_harness.uv_available():
        pytest.skip("uv is not available; CI installs it and is the authority for SC execution")
    tmp_root = tmp_path_factory.mktemp("sc01")
    instance, business_server, _calls = runner_harness.start_runner(tmp_root)
    yield instance
    instance.stop()
    runner_harness.stop_business_api(business_server)


def test_sc01_clean_venv_has_no_full_hecate(runner_stack: runner_harness.RunnerInstance) -> None:
    """The wheel closure must not contain the full hecate application."""

    result = subprocess.run(
        [
            str(runner_stack.python_exe),
            "-m",
            "pip",
            "list",
            "--format",
            "freeze",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    installed = result.stdout.splitlines()
    assert not any(line.lower().startswith("hecate==") for line in installed), installed
    assert any(line.lower().startswith("hecate-runtime==") for line in installed), installed
    assert any(line.lower().startswith("hecate-runner==") for line in installed), installed


def test_sc01_health_reports_ready_profile(runner_stack: runner_harness.RunnerInstance) -> None:
    status, body = runner_stack.request("GET", "/healthz", token=None)
    assert status == 200
    assert body["ready"] is True
    assert body["backend_type"] == "pregel"
    assert body["model_source"] == "stub"


def test_sc01_capabilities_declare_preview_limits(runner_stack: runner_harness.RunnerInstance) -> None:
    status, body = runner_stack.request("GET", "/capabilities", token=None)
    assert status == 200
    assert body["submit"] == "enforced"
    for capability in ("durable_tasks", "background_retry", "long_task_recovery", "write_tools", "approval_tools"):
        assert body[capability].startswith("unsupported"), capability


def test_sc01_clean_install_carries_contract_schemas(runner_stack: runner_harness.RunnerInstance) -> None:
    result = subprocess.run(
        [
            str(runner_stack.python_exe),
            "-c",
            "from hecate_runner.contract import bundled_schema_names; print('\\n'.join(bundled_schema_names()))",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    schemas = tuple(result.stdout.splitlines())
    assert len(schemas) == 16
    assert "execution-request.schema.json" in schemas
    assert "capabilities.schema.json" in schemas


def test_sc01_formal_contract_submission_works_without_full_hecate(
    runner_stack: runner_harness.RunnerInstance,
) -> None:
    request = {
        "contract_version": "0.1",
        "task_ref": {"kind": "task", "issuer_domain": "hecate-platform", "id": "sc01-task"},
        "run_ref": {"kind": "run", "issuer_domain": "hecate-platform", "id": "sc01-run"},
        "deployment_ref": {
            "kind": "deployment",
            "issuer_domain": "hecate-platform",
            "id": "sc01-deployment",
        },
        "authorization_ref": {
            "kind": "authorization",
            "issuer_domain": "hecate-platform",
            "id": "sc01-authorization",
        },
        "input": {
            "prompt": "check stock for SKU-A1",
            "tool_arguments": {"domain": "domain_a", "sku": "SKU-A1"},
        },
        "idempotency_key": "sc01-contract-key",
        "trace_correlation": {"trace_id": "sc01-contract-trace"},
    }
    status, receipt = runner_stack.request(
        "POST",
        "/runs",
        request,
        headers={"Idempotency-Key": request["idempotency_key"]},
    )
    assert status == 202, receipt
    assert receipt["run_ref"]["kind"] == "run"
    assert receipt["run_ref"]["issuer_domain"] == "standalone-host"
    wire_ref = urllib.parse.quote(f"{receipt['run_ref']['issuer_domain']}/{receipt['run_ref']['id']}", safe="")

    final_status, run = runner_stack.wait_run(receipt["run_ref"]["id"])
    assert final_status == 200
    assert run["status"] == "succeeded"
    _, formal_run = runner_stack.request("GET", f"/runs/{wire_ref}")
    assert formal_run["state"] == "succeeded"
    _, events = runner_stack.request("GET", f"/runs/{wire_ref}/events")
    assert events["events"] and events["next_cursor"]
    assert all(event["source"] == "execution_backend" for event in events["events"])
    _, artifacts = runner_stack.request("GET", f"/runs/{wire_ref}/artifacts")
    assert all(ref["kind"] == "artifact" for ref in artifacts["artifacts"])


def test_sc01_read_only_run_completes_without_control_plane(runner_stack: runner_harness.RunnerInstance) -> None:
    """No control-plane address exists anywhere in the profile; the read
    run still completes and leaves a result reference plus local evidence."""

    profile_config = (runner_stack.profile_dir / "runner.json").read_text(encoding="utf-8")
    assert "control_plane" not in profile_config
    assert "management" not in profile_config

    status, submit = runner_stack.request(
        "POST",
        "/runs",
        {
            "input": {"prompt": "check stock for SKU-A1"},
            "tool_arguments": {"domain": "domain_a", "sku": "SKU-A1"},
        },
    )
    assert status == 202, submit
    run_id = submit["run_ref"].split("/")[1]

    final_status, run = runner_stack.wait_run(run_id)
    assert final_status == 200
    assert run["status"] == "succeeded", run
    assert run["result_ref"] == f"runs/{run_id}/artifacts"

    _, events = runner_stack.request("GET", f"/runs/{run_id}/events")
    tool_results = [event for event in events["events"] if event.get("type") == "tool_result"]
    assert tool_results, "expected the read tool to execute"
    assert tool_results[-1]["outcome"]["status"] == "ok"

    _, evidence = runner_stack.request("GET", "/v1/evidence?outcome=ok")
    assert any(record["ref"] == run_id for record in evidence["records"]), evidence
