"""Hermetic checks of the recorded comparison; no real model calls in CI."""

from __future__ import annotations

import json
from types import SimpleNamespace

from jsonschema import Draft202012Validator

from tests.scenarios.baseline.run_autonomous_baseline import MATERIAL, REPORT_SCHEMA, run_real_model_task


async def test_missing_key_is_explicit_skip(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    report = await run_real_model_task()
    Draft202012Validator(REPORT_SCHEMA).validate(report)
    assert report["_meta"]["status"] == "skipped"
    assert report["runs"] == [] and report["_meta"]["gate"] is False


async def test_report_compares_actual_paths_and_does_not_invent_cost():
    async def completion(**kwargs):
        messages = kwargs["messages"]
        results = [m for m in messages if m.get("role") == "tool"]
        if len(results) == 0:
            name, args = "lookup_material", {"domain": "domain_a", "sku": "SKU-A1"}
        elif len(results) == 1:
            name, args = "create_test_ticket", {"title": "reorder", "body": MATERIAL["ticket_body"]}
        else:
            name = None
            args = {}
        calls = (
            [
                {
                    "id": "call-" + str(len(results)),
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
            ]
            if name
            else []
        )
        message = SimpleNamespace(content='{"answer":"TKT-BASELINE"}' if not calls else "", tool_calls=calls)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage={"total_tokens": 10})

    report = await run_real_model_task(completion)
    Draft202012Validator(REPORT_SCHEMA).validate(report)
    assert len(report["runs"]) == 8
    for run in report["runs"]:
        assert run["summary"]["tool_selection_correct"], run
        assert run["summary"]["stop_compliance"], run
        assert run["summary"]["within_cost_threshold"] is None
        assert run["usage"]["cost_amount"] is None
        if run["case"] == "normal":
            assert run["summary"]["structured_output_valid"]
            assert run["summary"]["artifact_reference_correct"]
            assert run["side_effect_count"] == 1
