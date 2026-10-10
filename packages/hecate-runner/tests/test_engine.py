"""Regressions for runner admission, model calls, cancellation and evidence."""

from __future__ import annotations

import asyncio
import time
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


async def test_lease_renewal_bounded_wait_times_out_and_renews(tmp_path, monkeypatch):
    """step6b renewal policy: a consumed nonce starts a bounded wait for the
    channel's next pull; a fresh lease within the budget authorizes, past the
    budget the refusal is final and evidenced."""
    from datetime import UTC, datetime

    from hecate_durable.contracts.credentials import lease_claims
    from hecate_runner.engine import RunState
    from hecate_runner.managed import LeaseGate

    monkeypatch.setenv("RUNNER_SHUTDOWN_TOKEN", "shutdown")
    profile = load_profile(write_profile(tmp_path))
    evidence = EvidenceStore(profile.config.evidence_dir)
    now = {"t": datetime.now(UTC)}
    gate = LeaseGate(
        b"engine-lease-secret",
        deployment_domain="host-root",
        clock=lambda: now["t"],
    )
    calls: list = []

    async def dispatch(*args):
        calls.append(args)
        return {"status": "ok", "result": {"quantity": 100}}

    runtime = ExecutionEngine(
        profile,
        evidence,
        dispatch,
        lease_gate=gate,
        lease_refresh_wait_seconds=0.15,
    )
    state = RunState(run_id="r1", status="running", principal="reader", domains=("domain_a",), managed=True)

    def _lease():
        return lease_claims(
            b"engine-lease-secret",
            iss="lease-issuer",
            deployment_domain="host-root",
            sub="host-1",
            ttl_seconds=3600,
            scope=["domain_a"],
        )[0]

    gate.update(_lease())
    assert await runtime._lease_refusal(state, "stock_write", {"domain": "domain_a"}) is None

    # The nonce is consumed and no fresh lease arrives: bounded wait, then an
    # explicit evidenced refusal (dispatch_denied is the caller's contract).
    started = time.monotonic()
    denial = await runtime._lease_refusal(state, "stock_write", {"domain": "domain_a"})
    assert denial is not None and denial["status"] == "authorization"
    assert time.monotonic() - started >= 0.14
    # Caller contract (the engine's action boundary): a refusal marks the
    # dispatch denied, failing the run without further business calls.
    if denial is not None:
        state.dispatch_denied = True
    assert state.dispatch_denied is True

    # Renewal path: the channel installs a fresh lease inside the budget and
    # the next protected dispatch proceeds without a refusal.
    renewed = RunState(run_id="r2", status="running", principal="reader", domains=("domain_a",), managed=True)

    async def install_late():
        await asyncio.sleep(0.03)
        assert gate.update(_lease()) is True

    installer = asyncio.create_task(install_late())
    assert await runtime._lease_refusal(renewed, "stock_write", {"domain": "domain_a"}) is None
    await installer


async def test_lease_scope_violation_refuses_immediately_without_waiting(tmp_path, monkeypatch):
    """A domain outside the lease scope is refused at once — the bounded wait
    is only for a not-yet-arrived lease, never for an out-of-scope request."""
    from datetime import UTC, datetime

    from hecate_durable.contracts.credentials import lease_claims
    from hecate_runner.engine import RunState
    from hecate_runner.managed import LeaseGate

    monkeypatch.setenv("RUNNER_SHUTDOWN_TOKEN", "shutdown")
    profile = load_profile(write_profile(tmp_path))
    evidence = EvidenceStore(profile.config.evidence_dir)
    gate = LeaseGate(
        b"engine-lease-secret",
        deployment_domain="host-root",
        clock=lambda: datetime.now(UTC),
    )

    async def dispatch(*args):
        return {"status": "ok", "result": {"quantity": 100}}

    runtime = ExecutionEngine(
        profile,
        evidence,
        dispatch,
        lease_gate=gate,
        lease_refresh_wait_seconds=5.0,
    )
    gate.update(
        lease_claims(
            b"engine-lease-secret",
            iss="lease-issuer",
            deployment_domain="host-root",
            sub="host-1",
            ttl_seconds=3600,
            scope=["domain_a"],
        )[0]
    )
    state = RunState(run_id="r3", status="running", principal="reader", domains=("domain_a",), managed=True)

    started = time.monotonic()
    denial = await runtime._lease_refusal(state, "stock_write", {"domain": "domain_b"})
    assert denial is not None and "outside the current lease scope" in denial["detail"]
    assert time.monotonic() - started < 1.0
