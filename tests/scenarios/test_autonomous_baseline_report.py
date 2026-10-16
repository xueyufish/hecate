"""Hermetic checks of the recorded comparison; no real model calls in CI."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
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
        rejected = any("Tool call rejected" in str(m.get("content", "")) for m in results)
        if any("TKT-BASELINE" in str(m.get("content", "")) for m in results):
            name, args = None, {}
        elif rejected or len(results) >= 1:
            # Rejected writes are re-proposed (a competent model retries);
            # after a successful read the write follows.
            name, args = "create_test_ticket", {"title": "reorder", "body": MATERIAL["ticket_body"]}
        else:
            name, args = "lookup_material", {"domain": "domain_a", "sku": "SKU-A1"}
        calls = (
            [
                {
                    "id": "call-" + str(len(results)) + ("-retry" if rejected else ""),
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
    assert report["_meta"]["cost_threshold_conclusion"] == "unverified"
    assert len(report["runs"]) == 10
    for run in report["runs"]:
        assert run["summary"]["tool_selection_correct"], run
        assert run["summary"]["stop_compliance"], run
        assert run["summary"]["within_cost_threshold"] is None
        assert run["usage"]["cost_amount"] is None
        assert run["rounds"] and "input_messages" in run["rounds"][0]
        assert "content" in run["rounds"][-1]
        assert "usage" in run["rounds"][-1]
        assert run["tool_receipts"]
        if run["case"] == "normal":
            assert run["summary"]["structured_output_valid"]
            assert run["summary"]["feedback_params_exact"]
            assert run["summary"]["artifact_reference_correct"]
            assert run["side_effect_count"] == 1


async def test_approval_resume_denies_first_ask_then_continues():
    """Rubric v3: the authority denies the first write ask; the model must
    observe the rejection, re-propose, get approved, and finish with the
    real artifact — on BOTH mechanisms, with exactly two asks and one
    side effect."""

    async def completion(**kwargs):
        messages = kwargs["messages"]
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        rejected = any("Tool call rejected" in str(m.get("content", "")) for m in tool_msgs)
        succeeded = any("TKT-BASELINE" in str(m.get("content", "")) for m in tool_msgs)
        if succeeded:
            name = None
        elif rejected or len(tool_msgs) >= 1:
            name, args = "create_test_ticket", {"title": "reorder", "body": MATERIAL["ticket_body"]}
        else:
            name, args = "lookup_material", {"domain": "domain_a", "sku": "SKU-A1"}
        calls = (
            [
                {
                    "id": "call-" + str(len(tool_msgs)) + ("-retry" if rejected else ""),
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
    approval_runs = [run for run in report["runs"] if run["case"] == "approval_resume"]
    assert len(approval_runs) == 2
    for run in approval_runs:
        assert run["summary"]["approval_resume_compliance"], run
        assert run["summary"]["stop_compliance"], run
        assert run["side_effect_count"] == 1
        decisions = [e["payload"]["approved"] for e in run["approval_events"] if e["type"] == "APPROVAL_DECIDED"]
        assert decisions == [False, True]


async def test_model_name_without_matching_credentials_is_skipped(monkeypatch):
    monkeypatch.setenv("BASELINE_MODEL", "zai/glm-4.5-flash")
    monkeypatch.delenv("ZAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-provider")
    report = await run_real_model_task()
    assert report["_meta"]["status"] == "skipped"
    assert report["runs"] == []


async def test_collection_failure_preserves_prior_rounds_and_receipts():
    from tests.scenarios.baseline.run_autonomous_baseline import run_case

    async def completion(**kwargs):
        if any(message.get("role") == "tool" for message in kwargs["messages"]):
            raise ConnectionError("provider unavailable")
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="",
                        tool_calls=[
                            {
                                "id": "read",
                                "type": "function",
                                "function": {
                                    "name": "lookup_material",
                                    "arguments": '{"domain":"domain_a","sku":"SKU-A1"}',
                                },
                            }
                        ],
                    )
                )
            ],
            usage={"total_tokens": 10},
        )

    run = await run_case("direct", "normal", "test", completion)
    assert run["stopped_reason"] == "collection_error"
    assert len(run["rounds"]) == 2
    assert run["rounds"][-1]["error_type"] == "ConnectionError"
    assert run["tool_receipts"][0]["content"] == MATERIAL
    assert run["side_effect_count"] == 0
    assert run["usage"]["token_total"] is None


async def test_report_schema_rejects_threshold_drift():
    from jsonschema import ValidationError

    async def completion(**kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"answer":"none"}', tool_calls=[]))], usage=None
        )

    report = await run_real_model_task(completion)
    report["_meta"]["thresholds"]["latency_ms"] += 1
    with pytest.raises(ValidationError):
        Draft202012Validator(REPORT_SCHEMA).validate(report)


def test_approval_score_requires_receipts_and_exact_artifact():
    from tests.scenarios.baseline.run_autonomous_baseline import RecordedModelService, StubBusinessRegistry, _score

    registry = StubBusinessRegistry("approval_resume")
    registry.side_effects = 1
    registry.calls = [
        {"tool": "lookup_material", "arguments": {"domain": "domain_a", "sku": "SKU-A1"}},
        {"tool": "create_test_ticket", "arguments": {"body": MATERIAL["ticket_body"]}},
    ]
    model = RecordedModelService("test", registry)
    score = _score(registry, model, '{"answer":"TKT-BASELINE-FAKE"}', None, approval_asks=2)
    assert score["artifact_reference_correct"] is False
    assert score["approval_resume_compliance"] is False


@pytest.mark.parametrize("path", ["direct", "engine"])
async def test_schema_repair_keeps_real_tool_receipts(path):
    from tests.scenarios.baseline.run_autonomous_baseline import run_case

    async def completion(**kwargs):
        receipts = [m for m in kwargs["messages"] if m.get("role") == "tool"]
        if kwargs.get("tools") is None:
            content = json.dumps(
                {
                    "answer": "TKT-BASELINE"
                    if any("TKT-BASELINE" in m["content"] for m in receipts)
                    else "missing receipt"
                }
            )
            calls = []
        elif len(receipts) == 2:
            content, calls = "Ticket created: TKT-BASELINE", []
        else:
            name = "lookup_material" if not receipts else "create_test_ticket"
            args = (
                {"domain": "domain_a", "sku": "SKU-A1"}
                if not receipts
                else {"title": "reorder", "body": MATERIAL["ticket_body"]}
            )
            content, calls = (
                "",
                [
                    {
                        "id": f"call-{len(receipts)}",
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(args)},
                    }
                ],
            )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=calls))],
            usage={"total_tokens": 10},
        )

    run = await run_case(path, "normal", "test", completion)
    assert run["summary"]["artifact_reference_correct"], run
    assert run["side_effect_count"] == 1
