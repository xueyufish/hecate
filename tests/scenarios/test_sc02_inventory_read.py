"""SC02: structured inventory read and unauthorized calls.

The business-API fixture enforces its own rules (domain isolation,
server-side roles); the runner must not grant anything beyond the
server-verified identity: forged role claims are ignored, cross-domain
reads are refused before dispatch, unknown credentials are denied
with queryable evidence, invalid arguments fail before any dispatch,
and a manifest declaring a write tool fails startup.
"""

from __future__ import annotations

import json

import pytest

from tests.scenarios.tools import runner_harness


@pytest.fixture(scope="module")
def runner_stack(tmp_path_factory: pytest.TempPathFactory):
    if not runner_harness.uv_available():
        pytest.skip("uv is not available; CI installs it and is the authority for SC execution")
    tmp_root = tmp_path_factory.mktemp("sc02")
    instance, business_server, calls = runner_harness.start_runner(tmp_root)
    yield instance, calls
    instance.stop()
    runner_harness.stop_business_api(business_server)


def _submit(runner: runner_harness.RunnerInstance, arguments: dict, *, token: str | None = runner_harness.READER_TOKEN):
    status, body = runner.request(
        "POST",
        "/runs",
        {"input": {"prompt": "inventory check"}, "tool_arguments": arguments},
        token=token,
    )
    if status != 202:
        return status, body, None
    run_id = body["run_ref"].split("/")[1]
    _, run = runner.wait_run(run_id)
    _, events = runner.request("GET", f"/runs/{run_id}/events")
    return status, run, (run_id, events)


def test_sc02_authorized_domain_read_succeeds(runner_stack) -> None:
    runner, _ = runner_stack
    status, run, (run_id, events) = _submit(runner, {"domain": "domain_a", "sku": "SKU-A1"})
    assert status == 202 and run["status"] == "succeeded", run
    tool_results = [e for e in events["events"] if e.get("type") == "tool_result"]
    assert tool_results[-1]["outcome"]["status"] == "ok"
    assert tool_results[-1]["outcome"]["result"]["quantity"] == 100


def test_sc02_cross_domain_read_denied_before_dispatch(runner_stack) -> None:
    runner, calls = runner_stack
    before = len(calls)
    status, run, (run_id, events) = _submit(runner, {"domain": "domain_b", "sku": "SKU-B1"})
    assert status == 202 and run["status"] == "succeeded"
    tool_results = [e for e in events["events"] if e.get("type") == "tool_result"]
    assert tool_results[-1]["outcome"]["status"] == "authorization"
    assert len(calls) == before, "the runner must not dispatch outside the trusted identity scope"


def test_sc02_forged_role_claim_is_ignored(runner_stack) -> None:
    runner, _ = runner_stack
    status, run, (run_id, events) = _submit(
        runner,
        {"domain": "domain_b", "sku": "SKU-B1", "role": "admin", "domains": ["domain_b"]},
    )
    # The body's role/domain claims must be dropped: the verified identity
    # still only covers domain_a, so the business API refuses domain_b.
    tool_results = [e for e in events["events"] if e.get("type") == "tool_result"]
    assert tool_results[-1]["outcome"]["status"] == "authorization"


def test_sc02_unknown_credential_denied_with_evidence(runner_stack) -> None:
    runner, _ = runner_stack
    status, body = runner.request("POST", "/runs", {"input": {}}, token="not-a-real-token")
    assert status == 401
    assert body["type"].endswith("unauthenticated")
    # Unknown callers have no authenticated owner. The deployment operator
    # reads their denial evidence locally; a reader cannot query other owners.
    records = [
        json.loads(line)
        for path in (runner.profile_dir / "evidence").glob("evidence-*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert any(record["detail"].get("reason") == "unauthenticated" for record in records)


def test_sc02_invalid_arguments_fail_before_dispatch(runner_stack) -> None:
    runner, calls = runner_stack
    before = len(calls)
    status, body = runner.request("POST", "/runs", {"input": {}, "tool_arguments": ["not", "an", "object"]})
    assert status == 422
    assert body["type"].endswith("invalid-request")
    assert len(calls) == before, "the business API must not be called when arguments are invalid"
    _, evidence = runner.request("GET", "/v1/evidence?outcome=denied")
    assert any(record["detail"]["reason"] == "invalid-request" for record in evidence["records"])


def test_sc02_write_manifest_fails_startup(tmp_path_factory: pytest.TempPathFactory) -> None:
    """A profile whose manifest declares a write tool must not start."""

    if not runner_harness.uv_available():
        pytest.skip("uv is not available; CI installs it and is the authority for SC execution")
    tmp_root = tmp_path_factory.mktemp("sc02-write")
    with pytest.raises(RuntimeError, match="write"):
        runner_harness.start_runner(
            tmp_root,
            manifest_tools=[{"name": "update_record", "schema_ref": "schemas/update.json", "permission": "write"}],
        )
