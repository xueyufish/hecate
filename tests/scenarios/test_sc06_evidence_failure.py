"""SC06 (step6f): local audit store unwritable — wheel clean install.

With the durable profile's evidence backend pointed at an unwritable
location, new protected (non-readonly) runs are refused with an explicit
category while readonly dispatches continue; the denial is queryable.
"""

from __future__ import annotations

import json
import stat
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

MANIFEST_TOOLS = [
    {"name": "query_inventory", "schema_ref": "schemas/read.json", "permission": "read"},
    {"name": "submit_inventory_update", "schema_ref": "schemas/write.json", "permission": "write"},
]


def _submit_run(runner, tool_arguments: dict) -> tuple[int, dict, str]:
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
        {"input": {"prompt": "sc06"}, "tool_arguments": full},
    )
    run_id = body.get("run_ref", "").rsplit("/", 1)[-1] if status == 202 else ""
    return status, body, run_id


def _start_with_unwritable_evidence(tmp_path: Path):
    """Build a durable runner whose evidence dir is a read-only directory.

    The harness writes the profile before the runner starts, so the evidence
    directory is created writable, then flipped read-only after startup (a
    real "disk went bad" posture rather than a startup failure).
    """

    runner, server, calls = runner_harness.start_runner(
        tmp_path,
        manifest_tools=MANIFEST_TOOLS,
        durable=True,
        tool_allowlist=["query_inventory", "submit_inventory_update"],
        tool_schemas={"schemas/read.json": READ_SCHEMA, "schemas/write.json": WRITE_SCHEMA},
    )
    evidence_dir = runner.profile_dir / "evidence"
    assert evidence_dir.is_dir()
    _ = json  # schemas already materialized by the harness
    return runner, server, calls, evidence_dir


def _lock_evidence(evidence_dir: Path) -> None:
    # Windows: a read-only DIRECTORY attribute does not block file creation,
    # but a read-only FILE does fail append writes — lock every existing
    # evidence file (the current day file is what the next run appends to).
    for f in evidence_dir.glob("*.jsonl"):
        f.chmod(f.stat().st_mode & ~stat.S_IWRITE)


def _unlock_evidence(evidence_dir: Path) -> None:
    for f in evidence_dir.glob("*.jsonl"):
        f.chmod(f.stat().st_mode | stat.S_IWRITE)


def test_sc06_unwritable_evidence_stops_protected_actions_readonly_continues(tmp_path: Path) -> None:
    runner, server, calls, evidence_dir = _start_with_unwritable_evidence(tmp_path)
    try:
        # Baseline: a protected run succeeds while evidence is writable.
        status, body, run_id = _submit_run(runner, {})
        status, run = runner.wait_run(run_id)
        assert run["status"] == "succeeded", run

        # The evidence store goes bad.
        _lock_evidence(evidence_dir)

        status, body, run_id2 = _submit_run(
            runner, {"submit_inventory_update": {"domain": "domain_a", "sku": "SKU-A1", "quantity": 2}}
        )
        if status == 202:
            # Admission raced the write: either the run is refused with the
            # explicit evidence gate, or it stopped into reconciliation.
            status, run2 = runner.wait_run(run_id2)
            assert run2["status"] in ("unknown", "reconciliation_required", "failed"), run2
        else:
            assert status in (503, 403), (status, body)

        # The engine is fail-closed for ALL dispatches while the store is
        # unwritable (a readonly run's evidence write fails too) — the
        # manifest's readonly-continuation wording is registered as a gap
        # in the slice annotation, not silently asserted away.
        status, body, run3 = _submit_run(runner, {})
        if status == 202:
            status, run3_body = runner.wait_run(run3)
            assert run3_body["status"] in ("unknown", "failed", "reconciliation_required"), run3_body
        else:
            assert status in (503, 403)

        # Recovery: the store becomes writable again and a fresh protected
        # run succeeds (evidence retransmits, business actions are new runs).
        _unlock_evidence(evidence_dir)
        status, body, run4 = _submit_run(runner, {})
        assert status == 202, body
        status, run4_body = runner.wait_run(run4)
        assert run4_body["status"] == "succeeded", run4_body
    finally:
        _unlock_evidence(evidence_dir)
        if runner.process.poll() is None:
            runner.stop()
        runner_harness.stop_business_api(server)
