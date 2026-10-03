"""Regressions for runner admission, model calls, cancellation and evidence."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx
import pytest
from conftest_runner import write_profile
from hecate_runner.engine import ExecutionEngine, ModelNodeWorker
from hecate_runner.evidence import EvidenceStore
from hecate_runner.profile import load_profile


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNNER_SHUTDOWN_TOKEN", "shutdown")
    profile = load_profile(write_profile(tmp_path))
    evidence = EvidenceStore(profile.config.evidence_dir)
    calls = []

    async def dispatch(*args):
        calls.append(args)
        return {"status": "ok", "result": {"quantity": 100}}

    return ExecutionEngine(profile, evidence, dispatch), calls


RUN_INPUT = {"input": {"prompt": "check stock"}, "tool_arguments": {"domain": "domain_a", "sku": "SKU-A1"}}


async def test_submit_reserves_serial_admission_before_scheduling(engine):
    runtime, calls = engine
    first, second = await asyncio.gather(
        runtime.submit("reader", RUN_INPUT, ("domain_a",)), runtime.submit("reader", RUN_INPUT, ("domain_a",))
    )
    assert first[1] is not None and second[1] is None
    await runtime.wait_for(first[0])
    assert len(calls) == 1


async def test_evidence_failure_refuses_execution(engine, monkeypatch):
    runtime, calls = engine

    def unavailable(*args, **kwargs):
        raise OSError("private evidence path is unavailable")

    monkeypatch.setattr(runtime._evidence, "append", unavailable)
    with pytest.raises(OSError):
        await runtime.submit("reader", RUN_INPUT, ("domain_a",))
    await asyncio.sleep(0)
    assert calls == [] and runtime._runs == {} and not runtime.busy


async def test_cancel_before_model_prevents_tool_dispatch(engine):
    runtime, calls = engine
    run_id, _ = await runtime.submit("reader", RUN_INPUT, ("domain_a",))
    assert runtime.request_cancel(run_id)
    state = await runtime.wait_for(run_id)
    assert state.status == "cancelled" and calls == []
    assert any(r.kind == "cancel" for r in runtime._evidence.query())


async def test_shutdown_records_unknown_without_orphan_tasks(engine, monkeypatch):
    runtime, calls = engine
    entered = asyncio.Event()

    async def slow_model(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(ModelNodeWorker, "execute", slow_model)
    run_id, _ = await runtime.submit("reader", RUN_INPUT, ("domain_a",))
    await entered.wait()
    await runtime.close()
    assert runtime.get_state(run_id).status == "unknown"
    assert not runtime._tasks and not runtime.busy and calls == []


async def test_shutdown_before_execution_starts_releases_slot_and_retains_evidence(engine):
    runtime, calls = engine
    run_id, _ = await runtime.submit("reader", RUN_INPUT, ("domain_a",))
    await runtime.close()
    assert runtime.get_state(run_id).status == "unknown"
    assert not runtime.busy and not runtime._tasks and calls == []
    assert runtime._evidence.query(kind="execution")[0].detail["status"] == "unknown"


async def test_terminal_success_is_not_visible_before_evidence_is_durable(engine, monkeypatch):
    runtime, calls = engine
    append = runtime._evidence.append
    observed = []

    def terminal_write_failure(kind, *args, **kwargs):
        if kind == "execution":
            observed.append(runtime.get_state(args[1]).status)
            raise OSError("cannot retain terminal evidence")
        return append(kind, *args, **kwargs)

    monkeypatch.setattr(runtime._evidence, "append", terminal_write_failure)
    run_id, _ = await runtime.submit("reader", RUN_INPUT, ("domain_a",))
    state = await runtime.wait_for(run_id)
    assert calls and observed == ["running"]
    assert state.status == "unknown" and state.result_ref is None


async def test_endpoint_mode_calls_configured_model_and_records_source(engine, monkeypatch):
    runtime, calls = engine
    requests = []

    def model(request):
        requests.append(request)
        return httpx.Response(200, json={"content": "inventory recommendation"})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(model)))
    runtime._profile = replace(
        runtime._profile,
        config=replace(
            runtime._profile.config,
            model_backend="endpoint",
            model_endpoint="https://model.example/invoke",
        ),
    )
    runtime._graph = runtime._compile_graph()
    run_id, _ = await runtime.submit("reader", RUN_INPUT, ("domain_a",))
    state = await runtime.wait_for(run_id)
    assert state.status == "succeeded" and len(requests) == 1 and len(calls) == 1
    assert b"check stock" in requests[0].content
    assert requests[0].url == "https://model.example/invoke"
    evidence = runtime._evidence.query(kind="execution")
    assert evidence[0].detail["model_source"] == "endpoint"


async def test_endpoint_failure_does_not_fall_back_to_stub(engine, monkeypatch):
    runtime, calls = engine
    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(lambda request: httpx.Response(500)),
        ),
    )
    runtime._profile = replace(
        runtime._profile,
        config=replace(
            runtime._profile.config,
            model_backend="endpoint",
            model_endpoint="https://model.example/invoke",
        ),
    )
    run_id, _ = await runtime.submit("reader", RUN_INPUT, ("domain_a",))
    state = await runtime.wait_for(run_id)
    assert state.status == "failed" and calls == []
    assert "model.example" not in state.error


def test_evidence_returns_newest_first(engine):
    runtime, _ = engine
    runtime._evidence.append("execution", "reader", "first", "ok")
    runtime._evidence.append("execution", "reader", "second", "ok")
    assert [record.ref for record in runtime._evidence.query(limit=1)] == ["second"]
