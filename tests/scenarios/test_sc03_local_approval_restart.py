"""SC03 (step6f): local approved write and restart — wheel clean install.

The durable runner is built from wheels, installed into a fresh venv (no
repo source path), and driven as a real subprocess. A protected write
executes exactly once under the durable ledger; a hard process kill and
restart over the same database backfills decided actions without redoing
them (business-side call counting), and durable facts never get rewritten.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.scenarios.tools import runner_harness

pytestmark = [
    pytest.mark.skipif(
        not runner_harness.uv_available(),
        reason="uv is required for the clean-install harness",
    )
]

READ_SCHEMA = {
    "type": "object",
    "required": ["domain", "sku"],
    "properties": {"domain": {"type": "string", "minLength": 1}, "sku": {"type": "string", "minLength": 1}},
}
WRITE_SCHEMA = {
    "type": "object",
    "required": ["domain", "sku", "quantity"],
    "properties": {
        "domain": {"type": "string", "minLength": 1},
        "sku": {"type": "string", "minLength": 1},
        "quantity": {"type": "integer", "minimum": 1},
    },
}

TOOL_SCHEMAS = {
    "schemas/read.json": READ_SCHEMA,
    "schemas/write.json": WRITE_SCHEMA,
}
MANIFEST_TOOLS = [
    {"name": "query_inventory", "schema_ref": "schemas/read.json", "permission": "read"},
    {"name": "submit_inventory_update", "schema_ref": "schemas/write.json", "permission": "write"},
]


@pytest.fixture
def harness(tmp_path: Path):
    runner, server, calls = runner_harness.start_runner(
        tmp_path,
        manifest_tools=MANIFEST_TOOLS,
        durable=True,
        tool_allowlist=["query_inventory", "submit_inventory_update"],
        tool_schemas=TOOL_SCHEMAS,
    )
    yield {"runner": runner, "calls": calls, "server": server, "profile": runner.profile_dir}
    if runner.process.poll() is None:
        runner.stop()
    runner_harness.stop_business_api(server)


def _write_calls(calls: list[dict]) -> list[dict]:
    return [c for c in calls if (c.get("arguments") or {}).get("quantity") is not None]


def _submit_run(runner, tool_arguments: dict) -> str:
    # The fixed tool plan validates every allowlisted tool, so absent tools
    # carry their minimal arguments.
    full = {
        "query_inventory": {"domain": "domain_a", "sku": "SKU-A1"},
        "submit_inventory_update": {"domain": "domain_a", "sku": "SKU-A1", "quantity": 1},
    }
    full.update(tool_arguments)
    status, body = runner.request(
        "POST",
        "/runs",
        {"input": {"prompt": "sc03 approved write"}, "tool_arguments": full},
    )
    assert status == 202, body
    return body["run_ref"].rsplit("/", 1)[-1]


def test_sc03_approved_write_executes_once_and_survives_hard_restart(harness) -> None:
    runner = harness["runner"]
    calls = harness["calls"]

    run_id = _submit_run(
        runner,
        {
            "query_inventory": {"domain": "domain_a", "sku": "SKU-A1"},
            "submit_inventory_update": {"domain": "domain_a", "sku": "SKU-A1", "quantity": 3},
        },
    )
    status, run = runner.wait_run(run_id)
    assert run["status"] == "succeeded", run

    # The protected write dispatched exactly once (business-side counting).
    assert len(_write_calls(calls)) == 1

    # Hard kill (no graceful drain) and restart over the same database: the
    # task's terminal fact is durable and NOTHING re-executes.
    runner = runner.restart(kill_previous=True)
    harness["runner"] = runner
    status, tasks = runner.request("GET", "/tasks")
    assert status == 200
    match = next(t for t in tasks["tasks"] if t["run_ref"] == f"runs/{run_id}")
    assert match["state"] == "succeeded"
    assert len(_write_calls(calls)) == 1

    # The restarted host still serves new work normally (the ledger gate
    # re-admits fresh runs after recovery): a fresh run's protected write
    # dispatches again — legitimately, under its own attempt key.
    run2 = _submit_run(runner, {"submit_inventory_update": {"domain": "domain_a", "sku": "SKU-A1", "quantity": 5}})
    status, run2_body = runner.wait_run(run2)
    assert run2_body["status"] == "succeeded"
    assert len(_write_calls(calls)) == 2
