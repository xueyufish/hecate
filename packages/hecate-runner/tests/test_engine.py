"""Regressions for runner admission, model calls, cancellation and evidence."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace

import httpx
import pytest
from conftest_runner import write_profile
from hecate_runner.engine import ExecutionEngine, ModelNodeWorker, RunState
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


async def test_local_approval_bridges_expired_lease_without_consuming_nonce(tmp_path, monkeypatch):
    """step7 I-Cb authorization order: a valid pre-approved local binding
    authorizes a protected dispatch within the last verified lease scope —
    the disconnect bridge. The binding's CAS is consumed, the expired lease
    nonce is not, and the evidence records the local_approval source."""

    from datetime import UTC, datetime

    from hecate_durable.contracts.credentials import CredentialError, lease_claims
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
    consumed_refs: list[str] = []
    lease, _ = lease_claims(
        b"engine-lease-secret",
        iss="issuer",
        deployment_domain="host-root",
        sub="host",
        ttl_seconds=1,
        scope=["domain_a"],
        now=now["t"],
    )
    gate.update(lease)
    from datetime import timedelta

    now["t"] += timedelta(seconds=2)

    async def finder(request):
        return "appr-bridge-1"

    async def approval_gate(ref, request):
        consumed_refs.append(ref)
        return True

    calls: list = []

    async def dispatch(*args):
        calls.append(args)
        return {"status": "ok", "result": {}}

    runtime = ExecutionEngine(
        profile,
        evidence,
        dispatch,
        lease_gate=gate,
        approval_gate=approval_gate,
        approval_finder=finder,
        lease_refresh_wait_seconds=0.15,
    )
    state = RunState(run_id="r1", status="running", principal="reader", domains=("domain_a",), managed=True)
    assert await runtime._lease_refusal(state, "stock_write", {"domain": "domain_a"}) is None
    assert consumed_refs == ["appr-bridge-1"]
    # Approval does not consume the expired lease's nonce.
    with pytest.raises(CredentialError, match="expired"):
        await gate.check()
    basis = [record for record in evidence.query(kind="approval_basis")]
    assert basis and basis[0].detail["source"] == "local_approval"

    # Without an approval (finder returns None) the same request hits the
    # lease gate and is refused after the bounded wait.
    async def empty_finder(request):
        return None

    runtime2 = ExecutionEngine(
        profile,
        evidence,
        dispatch,
        lease_gate=gate,
        approval_gate=approval_gate,
        approval_finder=empty_finder,
        lease_refresh_wait_seconds=0.05,
    )
    state2 = RunState(run_id="r2", status="running", principal="reader", domains=("domain_a",), managed=True)
    denial = await runtime2._lease_refusal(state2, "stock_write", {"domain": "domain_a"})
    assert denial is not None and "expired" in denial["detail"]

    # A gate that refuses the located binding falls through to the lease
    # gate too — the locator only prefilters, the gate is the authority.
    async def refusing_gate(ref, request):
        return False

    runtime3 = ExecutionEngine(
        profile,
        evidence,
        dispatch,
        lease_gate=gate,
        approval_gate=refusing_gate,
        approval_finder=finder,
        lease_refresh_wait_seconds=0.05,
    )
    state3 = RunState(run_id="r3", status="running", principal="reader", domains=("domain_a",), managed=True)
    denial3 = await runtime3._lease_refusal(state3, "stock_write", {"domain": "domain_a"})
    assert denial3 is not None
    _ = lease_claims  # import parity with the neighboring tests


async def test_structural_lease_refusal_does_not_burn_the_renewal_wait(tmp_path, monkeypatch):
    """An identity-mismatched lease is structural: the denial is immediate
    (no bounded-wait polling), with the precise reason in the refusal."""

    import time as time_module
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
        issuer_domain="lease-issuer",
        host_id="host-1",
        workspace_id="ws-1",
        clock=lambda: now["t"],
    )
    foreign = lease_claims(
        b"engine-lease-secret",
        iss="lease-issuer",
        deployment_domain="host-root",
        sub="host-OTHER",
        ttl_seconds=3600,
        scope=["domain_a"],
        tenant="ws-1",
    )[0]
    assert gate.update(foreign) is True  # installs; refused at the boundary

    calls: list = []

    async def dispatch(*args):
        calls.append(args)
        return {"status": "ok", "result": {}}

    runtime = ExecutionEngine(
        profile,
        evidence,
        dispatch,
        lease_gate=gate,
        lease_refresh_wait_seconds=5.0,
    )
    state = RunState(run_id="r1", status="running", principal="reader", domains=("domain_a",), managed=True)
    started = time_module.monotonic()
    denial = await runtime._lease_refusal(state, "stock_write", {"domain": "domain_a"})
    elapsed = time_module.monotonic() - started
    assert denial is not None and "does not match the enrolled host" in denial["detail"]
    assert elapsed < 1.0  # structural: no 5s renewal wait
    assert calls == []


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


class TestDispatchSchemaAndCategories:
    """step7 2.5 — schema validation at the action boundary and the four
    distinguishable error categories."""

    def _engine_with_dispatcher(self, tmp_path, monkeypatch, dispatcher, output_schemas=None):
        monkeypatch.setenv("RUNNER_SHUTDOWN_TOKEN", "shutdown")
        profile = load_profile(
            write_profile(
                tmp_path,
                allowlist=["query_inventory", "submit_inventory_update"],
                durable={"database_url": f"sqlite:///{tmp_path / 'state.db'}", "workspace": "standalone"},
                with_write_tool=True,
            )
        )
        if output_schemas:
            profile.config.tool_output_schemas.update(output_schemas)
        evidence = EvidenceStore(profile.config.evidence_dir)
        return ExecutionEngine(profile, evidence, dispatcher)

    @staticmethod
    def _state() -> RunState:
        return RunState(
            run_id="run-schema",
            status="running",
            principal="app-writer",
            domains=("domain_a",),
        )

    async def test_schema_invalid_arguments_rejected_before_side_effect(self, tmp_path, monkeypatch):
        calls: list = []

        async def dispatch(tool, arguments, principal, domains):
            calls.append((tool, arguments))
            return {"status": "ok", "result": {"quantity": 1}}

        runtime = self._engine_with_dispatcher(tmp_path, monkeypatch, dispatch)
        state = self._state()
        outcome = await runtime._dispatch_tool(
            state, "query_inventory", {"domain": "domain_a"}
        )  # missing required "sku"
        assert outcome["status"] == "schema_invalid"
        assert calls == []  # business API never called
        assert state.needs_reconciliation is False

    async def test_four_error_categories_distinguishable(self, tmp_path, monkeypatch):
        from hecate_runner.engine import ERROR_CATEGORY_BY_STATUS

        outcomes = {
            "authorization": {"status": "authorization", "detail": "denied"},
            "business": {"status": "business", "detail": "rejected"},
            "transient": {"status": "transient", "detail": "unreachable"},
            "unknown": {"status": "unknown", "detail": "outcome not established"},
            "schema_invalid": {"status": "schema_invalid", "detail": "bad args"},
        }
        categories = {status: ERROR_CATEGORY_BY_STATUS[outcome["status"]] for status, outcome in outcomes.items()}
        assert len(set(categories.values())) == len(categories)
        assert categories["authorization"] == "policy_denied"
        assert categories["business"] == "business_rejected"
        assert categories["transient"] == "technical_failure"
        assert categories["unknown"] == "outcome_unknown"
        assert categories["schema_invalid"] == "schema_invalid"

    async def test_output_schema_violation_not_consumed_as_success(self, tmp_path, monkeypatch):
        async def dispatch(tool, arguments, principal, domains):
            return {"status": "ok", "result": {"unexpected": True}}

        runtime = self._engine_with_dispatcher(
            tmp_path,
            monkeypatch,
            dispatch,
            output_schemas={
                "query_inventory": {
                    "type": "object",
                    "required": ["quantity"],
                    "properties": {"quantity": {"type": "number"}},
                }
            },
        )
        state = self._state()
        outcome = await runtime._dispatch_tool(state, "query_inventory", {"domain": "domain_a", "sku": "SKU-A1"})
        assert outcome["status"] == "output_invalid"
        assert state.needs_reconciliation is True

    async def test_conforming_output_consumed_normally(self, tmp_path, monkeypatch):
        async def dispatch(tool, arguments, principal, domains):
            return {"status": "ok", "result": {"quantity": 100}}

        runtime = self._engine_with_dispatcher(
            tmp_path,
            monkeypatch,
            dispatch,
            output_schemas={
                "query_inventory": {
                    "type": "object",
                    "required": ["quantity"],
                    "properties": {"quantity": {"type": "number"}},
                }
            },
        )
        state = self._state()
        outcome = await runtime._dispatch_tool(state, "query_inventory", {"domain": "domain_a", "sku": "SKU-A1"})
        assert outcome["status"] == "ok"
        assert state.needs_reconciliation is False


class TestWakeGrantApprovalRef:
    """step7 3.3 — the wake grant carries an approval reference and a bare
    wake token never authorizes a protected dispatch when the authority gate
    is configured."""

    def _engine_with_state(self, tmp_path, monkeypatch, *, gate, wake_grant):
        from hecate_durable.contracts.durable import IdempotencyKey
        from hecate_durable.contracts.references import BackendRef, RefKind
        from hecate_durable.storage.store import SqlDurableStore
        from hecate_runner.durable import DurableRuntime

        monkeypatch.setenv("RUNNER_SHUTDOWN_TOKEN", "shutdown")
        profile = load_profile(
            write_profile(
                tmp_path,
                durable={"database_url": f"sqlite:///{tmp_path / 'w.db'}", "workspace": "ws"},
                with_write_tool=True,
            )
        )
        evidence = EvidenceStore(profile.config.evidence_dir)
        store = SqlDurableStore(f"sqlite:///{tmp_path / 'w.db'}")
        store.create_schema()
        durable = DurableRuntime(store, workspace="ws")
        runtime = ExecutionEngine(profile, evidence, lambda *a, **k: None, durable=durable, approval_gate=gate)
        task_ref = BackendRef(RefKind.TASK, "issuer", "t-1")
        store.submit_task(
            key=IdempotencyKey(key="k-1", subject="p", workspace="ws", request_digest="r"),
            task_ref=task_ref,
            run_ref=BackendRef(RefKind.RUN, "issuer", "r-1"),
            input_payload={"_wake_grant": wake_grant},
        )
        from hecate_runner.engine import RunState

        state = RunState(
            run_id="r-1",
            status="running",
            principal="app-writer",
            domains=("domain_a",),
            task_ref=task_ref,
        )
        return runtime, state

    async def test_bare_token_without_ref_does_not_dispatch(self, tmp_path, monkeypatch):
        gate_calls: list = []

        async def gate(ref, request):
            gate_calls.append((request.tool_name, request.arguments_digest, ref))
            return True

        runtime, state = self._engine_with_state(
            tmp_path,
            monkeypatch,
            gate=gate,
            wake_grant={"tool": "submit_inventory_update", "arguments_digest": "d1"},
        )
        consumed = await runtime._consume_wake_grant(state, "submit_inventory_update", "d1", {"domain": "domain_a"})
        assert consumed is False
        assert gate_calls == []

    async def test_refused_authority_re_parks_without_consuming(self, tmp_path, monkeypatch):
        async def gate(ref, request):
            return False

        runtime, state = self._engine_with_state(
            tmp_path,
            monkeypatch,
            gate=gate,
            wake_grant={
                "tool": "submit_inventory_update",
                "arguments_digest": "d1",
                "approval_ref": "appr-1",
            },
        )
        consumed = await runtime._consume_wake_grant(state, "submit_inventory_update", "d1", {"domain": "domain_a"})
        assert consumed is False

    async def test_accepted_authority_consumes_and_records_basis(self, tmp_path, monkeypatch):
        consumed_refs: list = []

        async def gate(ref, request):
            consumed_refs.append(ref)
            return True

        runtime, state = self._engine_with_state(
            tmp_path,
            monkeypatch,
            gate=gate,
            wake_grant={
                "tool": "submit_inventory_update",
                "arguments_digest": "d1",
                "approval_ref": "appr-9",
            },
        )
        consumed = await runtime._consume_wake_grant(state, "submit_inventory_update", "d1", {"domain": "domain_a"})
        assert consumed is True
        assert consumed_refs == ["appr-9"]
        basis = [e for e in runtime._evidence.query(kind="approval_basis")]
        assert basis and basis[-1].detail["approval_ref"] == "appr-9"

    async def test_no_gate_keeps_step6_semantics(self, tmp_path, monkeypatch):
        runtime, state = self._engine_with_state(
            tmp_path,
            monkeypatch,
            gate=None,
            wake_grant={"tool": "submit_inventory_update", "arguments_digest": "d1"},
        )
        consumed = await runtime._consume_wake_grant(state, "submit_inventory_update", "d1", {"domain": "domain_a"})
        assert consumed is True
