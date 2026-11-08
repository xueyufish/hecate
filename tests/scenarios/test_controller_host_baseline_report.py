"""Hermetic report tests for the Controller two-host baseline collector.

Every run drives the REAL host assemblies (platform durable dispatch with
its governance facade; the runner engine over its durable store) with a
deterministic injected provider. No test ever touches the network: the
real-mode gate produces an explicit skipped record, and the deterministic
injection runs measure the harness and the hosts' stop classifications —
never model quality.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator, ValidationError

from tests.scenarios.baseline import controller_host_runner as runner_driver
from tests.scenarios.baseline import controller_host_spec as spec
from tests.scenarios.baseline import run_controller_host_baseline as collector

pytestmark = pytest.mark.timeout(600)


@pytest.fixture(scope="module")
async def hermetic_report(tmp_path_factory):
    root = tmp_path_factory.mktemp("controller-host-baseline")
    return await collector.collect(provider=spec.DeterministicProvider(), root=root)


def _run(report: dict, host: str, case: str) -> dict:
    return next(run for run in report["runs"] if run["host"] == host and run["case"] == case)


async def test_missing_gate_is_explicit_skip(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv(collector.REAL_MODE_GATE_ENV, raising=False)
    report = await collector.collect(model="gpt-4o", root=tmp_path)
    assert report["_meta"]["status"] == "skipped"
    assert report["runs"] == []
    assert report["_meta"]["gate"] is False
    Draft202012Validator(spec.REPORT_SCHEMA).validate(report)


async def test_model_without_matching_credentials_is_skipped(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(collector.REAL_MODE_GATE_ENV, "1")
    monkeypatch.delenv("ZAI_API_KEY", raising=False)
    report = await collector.collect(model="zai/glm-4.5-flash", root=tmp_path)
    assert report["_meta"]["status"] == "skipped"
    assert "credentials" in report["_meta"]["reason"]
    Draft202012Validator(spec.REPORT_SCHEMA).validate(report)


async def test_environment_model_is_used_without_cli_override(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BASELINE_MODEL", "zai/test-model")
    monkeypatch.delenv(collector.REAL_MODE_GATE_ENV, raising=False)
    report = await collector.collect(root=tmp_path)
    assert report["_meta"]["model"] == "zai/test-model"
    assert report["_meta"]["source_revision"]["head"] != "unverified"
    assert report["_meta"]["output_schema_digest"] == spec.digest(spec.OUTPUT_SCHEMA)


@pytest.mark.parametrize("hosts", [(), ("typo",), ("runner", "runner")])
async def test_invalid_host_selection_is_rejected(hosts, tmp_path) -> None:
    with pytest.raises(ValueError, match="hosts"):
        await collector.collect(provider=spec.DeterministicProvider(), hosts=hosts, root=tmp_path)


def test_approval_score_rejects_repeated_completed_read() -> None:
    observation = {
        "task_status": "succeeded",
        "error_category": None,
        "tool_calls": [
            {"tool": spec.READ_TOOL, "arguments": spec.EXPECTED_READ},
            {"tool": spec.READ_TOOL, "arguments": spec.EXPECTED_READ},
            {"tool": spec.WRITE_TOOL, "arguments": spec.EXPECTED_WRITE},
        ],
        "final_content": json.dumps({"answer": spec.RECEIPT_ID}),
        "side_effect_count": 1,
        "write_attempts": 1,
        "approval": {"parked": True, "write_attempts_before_park": 0, "decisions": [{"approved": True}]},
        "rebuild": {"performed": True, "same_run": True},
        "post_resume_model_input_has_receipt": True,
        "post_resume_model_input_has_read_receipt": True,
    }
    observation["approval"]["authority_consumed"] = True
    assert spec.score_run("approval_resume", "runner", observation)["approval_resume_compliance"] is False


def test_report_covers_both_hosts_and_does_not_invent_cost(hermetic_report) -> None:
    report = hermetic_report
    assert report["_meta"]["source"] == "deterministic_injection"
    assert report["_meta"]["gate"] is False
    assert report["_meta"]["model_version"] == "unverified"
    assert {(run["host"], run["case"]) for run in report["runs"]} == {
        (host, case) for host in spec.HOSTS for case in spec.CASES
    }
    for run in report["runs"]:
        assert run["collection_error"] is None, run
        assert run["summary"]["stop_compliance"] is True, run
        assert run["usage"]["cost_amount"] is None
        assert run["summary"]["within_cost_threshold"] is None
        assert run["summary"]["within_latency_threshold"] is True
        assert run["task_id"] and run["run_id"]
        for model_round in run["rounds"]:
            assert model_round["input_tools"] == spec.tool_definitions()
        for action in run["ledger_actions"]:
            assert action["action_key"] and action["arguments_digest"]
        # The honest mechanism labels must downgrade the recovery scope.
        assert "not a PostgreSQL or cross-process recovery certification" in run["mechanisms"]["storage"]
    quality = report["_meta"]["collection"]["real_model"]
    assert quality["enabled"] is False and quality["quality_conclusion"] == "not_available"
    deterministic = report["_meta"]["collection"]["deterministic_injection"]
    assert deterministic["cases"] == 10 and deterministic["stop_compliance_passed"] == 10


def test_normal_and_approval_resume_pass_on_both_hosts(hermetic_report) -> None:
    for host in spec.HOSTS:
        normal = _run(hermetic_report, host, "normal")
        assert normal["summary"]["delivery_compliance"] is True, normal
        assert normal["summary"]["tool_selection_correct"] is True
        assert normal["summary"]["feedback_params_exact"] is True
        assert normal["summary"]["artifact_reference_correct"] is True
        assert normal["side_effect_count"] == 1 and normal["write_attempts"] == 1
        assert normal["stop"]["task_status"] == "succeeded"

        approval = _run(hermetic_report, host, "approval_resume")
        assert approval["summary"]["approval_resume_compliance"] is True, approval
        assert approval["approval"]["parked"] is True
        assert approval["approval"]["write_attempts_before_park"] == 0
        assert [decision["approved"] for decision in approval["approval"]["decisions"]] == [True]
        assert approval["rebuild"]["performed"] is True
        assert approval["rebuild"]["same_run"] is True
        assert approval["post_resume_model_input_has_receipt"] is True
        assert approval["post_resume_model_input_has_read_receipt"] is True
        if host == "runner":
            assert approval["approval"]["authority_consumed"] is True
        assert approval["side_effect_count"] == 1 and approval["write_attempts"] == 1


def test_stop_cases_classify_and_withhold_writes(hermetic_report) -> None:
    cancelled = {host: _run(hermetic_report, host, "cancelled") for host in spec.HOSTS}
    for host, run in cancelled.items():
        assert run["host"] == host
        assert run["stop"]["task_status"] == "cancelled", run
        assert run["write_attempts"] == 0 and run["side_effect_count"] == 0
        assert run["summary"]["cancel_compliance"] is True

    platform_budget = _run(hermetic_report, "platform", "budget_exhausted")
    assert platform_budget["stop"]["error_category"] == "controller_token_capped"
    assert platform_budget["model_calls"] == 0 and platform_budget["write_attempts"] == 0
    assert platform_budget["summary"]["budget_compliance"] is True
    runner_budget = _run(hermetic_report, "runner", "budget_exhausted")
    assert runner_budget["stop"]["task_status"] == "failed"
    assert "budget" in (runner_budget["stop"]["error"] or "")
    assert runner_budget["model_calls"] == 0 and runner_budget["write_attempts"] == 0
    assert runner_budget["summary"]["budget_compliance"] is True

    platform_unknown = _run(hermetic_report, "platform", "unknown_outcome")
    assert platform_unknown["stop"]["task_status"] == "reconciliation_required"
    assert platform_unknown["stop"]["reconciliation_reason"] == "action outcome is not established"
    assert platform_unknown["model_calls"] == 1 and platform_unknown["write_attempts"] == 0
    assert platform_unknown["summary"]["unknown_stop_compliance"] is True
    runner_unknown = _run(hermetic_report, "runner", "unknown_outcome")
    assert runner_unknown["stop"]["task_status"] == "reconciliation_required"
    assert runner_unknown["model_calls"] == 1 and runner_unknown["write_attempts"] == 0
    assert runner_unknown["summary"]["unknown_stop_compliance"] is True


def test_report_schema_rejects_threshold_drift(hermetic_report) -> None:
    drifted = json.loads(json.dumps(hermetic_report, default=str))
    drifted["_meta"]["thresholds"]["latency_ms"] = drifted["_meta"]["thresholds"]["latency_ms"] + 1
    with pytest.raises(ValidationError):
        Draft202012Validator(spec.REPORT_SCHEMA).validate(drifted)


def test_report_schema_rejects_sampling_drift(hermetic_report) -> None:
    drifted = json.loads(json.dumps(hermetic_report))
    drifted["_meta"]["sampling"]["max_tokens"] += 1
    with pytest.raises(ValidationError):
        Draft202012Validator(spec.REPORT_SCHEMA).validate(drifted)


async def test_real_collection_uses_deterministic_providers_for_stop_cases(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(collector.REAL_MODE_GATE_ENV, "1")
    monkeypatch.setenv("OPENAI_API_KEY", "unused-local-test")
    selected = []

    async def run_case(host, case, provider, root):
        source = "real_model" if isinstance(provider, collector.LiteLLMProvider) else "deterministic_injection"
        selected.append((case, source))
        return {
            "host": host,
            "case": case,
            "source": source,
            "collection_error": None,
            "task_id": None,
            "run_id": None,
            "rounds": [],
            "summary": {},
            "usage": {},
            "stop": {},
        }

    monkeypatch.setattr(collector, "_run_case", run_case)
    report = await collector.collect(model="test-model", root=tmp_path)
    assert all(
        source == ("real_model" if case in spec.QUALITY_CASES else "deterministic_injection")
        for case, source in selected
    )
    assert report["_meta"]["collection"]["real_model"]["quality"]["real_model_cases"] == 4
    assert report["_meta"]["collection"]["deterministic_injection"]["cases"] == 6


async def test_provider_request_limit_is_enforced() -> None:
    provider = collector._BoundedProvider(spec.DeterministicProvider())
    for _ in range(spec.REQUEST_LIMIT):
        await provider.complete(messages=[], tools=[])
    with pytest.raises(RuntimeError, match="limit"):
        await provider.complete(messages=[], tools=[])


async def test_request_budget_pool_is_inert_when_unarmed(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv(collector.REQUEST_BUDGET_ENV, raising=False)
    provider = collector.LiteLLMProvider("zai/unused")

    class _Message:
        content = "ok"
        tool_calls = None

    class _Choice:
        message = _Message()

    class _Response:
        choices = [_Choice()]
        model = "fake-budget-model"
        usage = None

    async def fake_acompletion(**kwargs):
        return _Response()

    import litellm

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    # No pool file is consulted or created; the call passes through.
    reply = await provider.complete(messages=[], tools=[])
    assert reply["provider_model"] == "fake-budget-model"
    assert collector.RequestBudgetPool.remaining() is None
    assert collector.RequestBudgetPool.armed() is False


async def test_request_budget_pool_consumes_across_instances(tmp_path, monkeypatch) -> None:
    pool = tmp_path / "budget.json"
    pool.write_text(json.dumps({"remaining": 3}), encoding="utf-8")
    monkeypatch.setenv(collector.REQUEST_BUDGET_ENV, str(pool))

    calls = []

    async def fake_acompletion(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[]))],
            usage=None,
            model="fake",
        )

    import litellm

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    first, second = collector.LiteLLMProvider("zai/unused"), collector.LiteLLMProvider("zai/unused")
    await first.complete(messages=[], tools=[])
    assert collector.RequestBudgetPool.remaining() == 2
    await second.complete(messages=[], tools=[])
    assert json.loads(pool.read_text(encoding="utf-8")) == {"remaining": 1}
    await first.complete(messages=[], tools=[])
    assert collector.RequestBudgetPool.remaining() == 0
    with pytest.raises(RuntimeError, match="budget exhausted"):
        await second.complete(messages=[], tools=[])
    # The exhausted pool still reads as zero; no negative write happened.
    assert collector.RequestBudgetPool.remaining() == 0
    assert len(calls) == 3


async def test_request_budget_pool_charges_failed_provider_calls(tmp_path, monkeypatch) -> None:
    pool = tmp_path / "budget.json"
    pool.write_text(json.dumps({"remaining": 1}), encoding="utf-8")
    monkeypatch.setenv(collector.REQUEST_BUDGET_ENV, str(pool))
    calls = []

    async def fake_acompletion(**kwargs):
        calls.append(kwargs)
        raise TimeoutError("provider timeout")

    import litellm

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    provider = collector.LiteLLMProvider("zai/unused")
    with pytest.raises(TimeoutError, match="provider timeout"):
        await provider.complete(messages=[], tools=[])
    with pytest.raises(RuntimeError, match="budget exhausted"):
        await provider.complete(messages=[], tools=[])
    assert len(calls) == 1
    assert collector.RequestBudgetPool.remaining() == 0


@pytest.mark.parametrize("balance", [True, False, -1, 1.5, "3", None])
def test_request_budget_pool_refuses_invalid_balance(balance, tmp_path, monkeypatch) -> None:
    pool = tmp_path / "budget.json"
    original = json.dumps({"remaining": balance})
    pool.write_text(original, encoding="utf-8")
    monkeypatch.setenv(collector.REQUEST_BUDGET_ENV, str(pool))
    with pytest.raises(RuntimeError, match="unreadable"):
        collector.RequestBudgetPool.consume()
    assert pool.read_text(encoding="utf-8") == original
    assert sorted(path.name for path in tmp_path.iterdir()) == ["budget.json"]


def test_request_budget_pool_refuses_locked_pool(tmp_path, monkeypatch) -> None:
    pool = tmp_path / "budget.json"
    pool.write_text(json.dumps({"remaining": 1}), encoding="utf-8")
    lock = tmp_path / ".budget.json.lock"
    lock.touch()
    monkeypatch.setenv(collector.REQUEST_BUDGET_ENV, str(pool))
    with pytest.raises(RuntimeError, match="pool locked"):
        collector.RequestBudgetPool.consume()
    assert collector.RequestBudgetPool.remaining() == 1
    assert lock.exists()


def test_request_budget_pool_write_failure_preserves_balance(tmp_path, monkeypatch) -> None:
    pool = tmp_path / "budget.json"
    pool.write_text(json.dumps({"remaining": 1}), encoding="utf-8")
    monkeypatch.setenv(collector.REQUEST_BUDGET_ENV, str(pool))

    def fail_replace(source, destination):
        raise OSError("replacement refused")

    monkeypatch.setattr(collector.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replacement refused"):
        collector.RequestBudgetPool.consume()
    assert collector.RequestBudgetPool.remaining() == 1
    assert sorted(path.name for path in tmp_path.iterdir()) == ["budget.json"]


def test_request_budget_pool_is_shared_between_processes(tmp_path, monkeypatch) -> None:
    pool = tmp_path / "budget.json"
    pool.write_text(json.dumps({"remaining": 5}), encoding="utf-8")
    monkeypatch.setenv(collector.REQUEST_BUDGET_ENV, str(pool))
    script = """
import time
from tests.scenarios.baseline.run_controller_host_baseline import RequestBudgetPool
count = 0
deadline = time.monotonic() + 10
while time.monotonic() < deadline:
    try:
        RequestBudgetPool.consume()
        count += 1
    except RuntimeError as error:
        if 'exhausted' in str(error):
            break
        if 'pool locked' not in str(error):
            raise
        time.sleep(0.01)
else:
    raise RuntimeError('budget workers did not finish')
print(count)
"""
    processes = [
        subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for _ in range(2)
    ]
    results = [process.communicate(timeout=30) for process in processes]
    assert [process.returncode for process in processes] == [0, 0], results
    assert sum(int(stdout.strip()) for stdout, _ in results) == 5
    assert collector.RequestBudgetPool.remaining() == 0
    assert sorted(path.name for path in tmp_path.iterdir()) == ["budget.json"]


def test_request_budget_pool_refuses_unreadable_file(tmp_path, monkeypatch) -> None:
    pool = tmp_path / "budget.json"
    pool.write_text("not-json", encoding="utf-8")
    monkeypatch.setenv(collector.REQUEST_BUDGET_ENV, str(pool))
    with pytest.raises(RuntimeError, match="unreadable"):
        collector.RequestBudgetPool.remaining()
    with pytest.raises(RuntimeError, match="unreadable"):
        collector.RequestBudgetPool.consume()


async def test_provider_token_limit_is_enforced() -> None:
    class MeteredProvider:
        async def complete(self, **kwargs):
            return {"usage": {"total_tokens": spec.TOKEN_BUDGET}}

    provider = collector._BoundedProvider(MeteredProvider())
    await provider.complete(messages=[], tools=[])
    with pytest.raises(RuntimeError, match="limit"):
        await provider.complete(messages=[], tools=[])


async def test_provider_failure_preserves_rounds_and_receipts(tmp_path) -> None:
    for host in spec.HOSTS:
        provider = spec.FailAfterProvider(spec.DeterministicProvider(), fail_after=1)
        report = await collector.collect(provider=provider, root=tmp_path, hosts=(host,), cases=("normal",))
        for run in report["runs"]:
            assert run["rounds"], "prior rounds must survive the provider outage"
            assert run["rounds"][0]["input_messages"]
            assert run["rounds"][-1].get("error_type") == "ConnectionError"
            assert run["tool_receipts"], "recorded receipts must survive the provider outage"
            assert run["write_attempts"] == 0 and run["side_effect_count"] == 0
            assert run["usage"]["token_total"] is None


async def test_cancel_case_records_early_final_instead_of_waiting_forever(tmp_path) -> None:
    class FinalProvider:
        name = "early-final"

        async def complete(self, **kwargs):
            return {"content": "done", "tool_calls": [], "usage": None}

    report = await asyncio.wait_for(
        collector.collect(provider=FinalProvider(), root=tmp_path, hosts=("runner",), cases=("cancelled",)),
        timeout=15,
    )
    run = report["runs"][0]
    assert "before the read action started" in run["collection_error"]
    assert run["rounds"]
    assert run["summary"]["cancel_compliance"] is False


async def test_endpoint_shutdown_cancels_inflight_provider() -> None:
    import httpx

    entered = asyncio.Event()
    cancelled = asyncio.Event()

    class HangingProvider:
        async def complete(self, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    server = runner_driver._EndpointModelServer(HangingProvider())
    await server.start()
    async with httpx.AsyncClient() as client:
        request = asyncio.create_task(client.post(server.url, json={"messages": [], "tools": []}))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            await server.stop()
            assert cancelled.is_set()
            assert not server._handlers
        finally:
            request.cancel()
            await asyncio.gather(request, return_exceptions=True)
            await server.stop()
