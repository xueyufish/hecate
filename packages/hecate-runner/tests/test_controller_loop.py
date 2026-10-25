"""Controller-loop opt-in on the runner: same controller, runner assembly.

Covers the explicit ``controller_loop`` binding end to end over the real
runner durable assembly: the loop selects tools autonomously from the
model's structured answer (not the fixed graph plan), a policy-required
approval parks the durable task through the signal path with the business
side effect withheld, and an authorized wake continues the SAME decision
chain — replaying the committed turn from storage without re-asking the
model or re-executing completed actions. The model is a scripted local
HTTP endpoint (the profile's declared backend), not a stub result.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import pytest
from conftest_runner import write_profile
from hecate_durable.contracts.durable import TaskLifecycleState
from hecate_runner.durable import DurableRuntime
from hecate_runner.engine import ExecutionEngine
from hecate_runner.evidence import EvidenceStore
from hecate_runner.profile import load_profile


@pytest.fixture(autouse=True)
def _shutdown_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNNER_SHUTDOWN_TOKEN", "shutdown-token-value")


class _ScriptedModelServer:
    """Local HTTP model endpoint scripted by projected position.

    Turn 1 requests the declared tool; after the tool result arrives the
    next call answers with the final text. Requests/answers are recorded
    so tests can assert the loop only decided what the script allowed.
    """

    def __init__(self, tool_name: str, tool_arguments: dict, final_text: str, *, tool_turns: int = 1) -> None:
        self.tool_name = tool_name
        self.tool_arguments = tool_arguments
        self.final_text = final_text
        self.tool_turns = tool_turns
        self.requests: list[dict] = []
        self.answers: list[dict] = []
        self.response_override: dict | None = None
        self._server: asyncio.AbstractServer | None = None
        self.url: str = ""

    async def start(self) -> str:
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}/invoke"
        return self.url

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.readline()  # request line; routing is fixed here
            headers: dict[str, str] = {}
            while True:
                header = await reader.readline()
                if header in (b"\r\n", b"\n", b""):
                    break
                name, _, value = header.decode("latin-1").partition(":")
                headers[name.strip().lower()] = value.strip()
            body = await reader.readexactly(int(headers.get("content-length", "0")))
            request = json.loads(body.decode("utf-8"))
            self.requests.append(request)
            messages = request.get("messages") or []
            tool_messages = [m for m in messages if isinstance(m, dict) and m.get("role") == "tool"]
            if len(tool_messages) < self.tool_turns:
                arguments = dict(self.tool_arguments)
                if self.tool_turns > 1 and "sku" in arguments:
                    arguments["sku"] = f"{arguments['sku']}-{len(tool_messages)}"
                answer = {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": self.tool_name,
                                "arguments": json.dumps(arguments),
                            },
                        }
                    ],
                }
            else:
                answer = {"content": self.final_text}
            if self.response_override is not None:
                answer = self.response_override
            self.answers.append(answer)
            payload = json.dumps(answer).encode("utf-8")
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                + f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
                + payload
            )
            await writer.drain()
        except Exception:  # noqa: BLE001, S110 — connection-level failures end the exchange
            pass
        finally:
            writer.close()


class _BusinessApi:
    """Records every dispatch with its server-verified scope."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, tool_name: str, arguments: dict, principal: str, domains: list[str]) -> dict:
        self.calls.append({"tool": tool_name, "arguments": dict(arguments), "principal": principal})
        return {"status": "ok", "result": f"{tool_name}-result"}


class _Harness:
    """One runner assembly: durable store + engine (no HTTP surface)."""

    def __init__(self, profile_dir: Path, api: _BusinessApi) -> None:
        self.profile = load_profile(profile_dir)
        self.evidence = EvidenceStore(self.profile.config.evidence_dir)
        self.durable = DurableRuntime(
            _open_durable_store(self.profile.config.durable.database_url),
            workspace=self.profile.config.durable.workspace,
        )
        self.engine = ExecutionEngine(self.profile, self.evidence, api, durable=self.durable)


def _open_durable_store(database_url: str):
    from hecate_durable.storage import SqlDurableStore

    store = SqlDurableStore(database_url)
    store.create_schema()
    return store


@pytest.fixture
def loop_profile(tmp_path: Path) -> Path:
    return write_profile(
        tmp_path,
        durable={"database_url": f"sqlite:///{tmp_path / 'controller.db'}", "workspace": "ws-a"},
        controller_loop=True,
    )


async def test_loop_selects_tools_from_model_answer_and_finishes(tmp_path, loop_profile) -> None:
    """The model's structured choice drives the action; no fixed plan."""
    api = _BusinessApi()
    model = _ScriptedModelServer(
        "query_inventory", {"domain": "domain_a", "sku": "CHOSEN-BY-MODEL"}, "final controller answer"
    )
    await model.start()
    try:
        # Point the profile's declared endpoint at the scripted model.
        config = json.loads((loop_profile / "runner.json").read_text(encoding="utf-8"))
        config["model"] = {"backend": "endpoint", "endpoint": model.url}
        (loop_profile / "runner.json").write_text(json.dumps(config), encoding="utf-8")
        harness = _Harness(loop_profile, api)
        run_id, state = await harness.engine.submit("app-reader", {"input": {"prompt": "check stock"}}, ("domain_a",))
        assert state is not None
        final = await harness.engine.wait_for(run_id)
        assert final.status == "succeeded"
        assert final.result_ref == f"runs/{run_id}/artifacts"
        # The business call used the MODEL's arguments — autonomous choice.
        assert api.calls == [
            {
                "tool": "query_inventory",
                "arguments": {"domain": "domain_a", "sku": "CHOSEN-BY-MODEL"},
                "principal": "app-reader",
            }
        ]
        assert [a["content"] for a in model.answers] == ["", "final controller answer"]
        event_types = [event.get("type") for event in final.events]
        assert "controller/turn_started" in event_types
        assert "controller/final_answer" in event_types
        assert len(harness.durable.store.list_run_actions(final.run_ref)) == 1
    finally:
        await model.stop()


@pytest.mark.parametrize("tool_turns", [1, 2])
async def test_approval_park_withholds_effect_and_wake_continues_chain(tmp_path, tool_turns) -> None:
    """A policy-required approval parks durably; the wake replays the chain."""
    profile_dir = write_profile(
        tmp_path,
        allowlist=["query_inventory", "submit_ticket"],
        durable={"database_url": f"sqlite:///{tmp_path / 'controller-wait.db'}", "workspace": "ws-a"},
        with_approval_tool=True,
        controller_loop=True,
    )
    api = _BusinessApi()
    # The runner's identity-scope contract requires dispatch arguments to
    # carry the server-verified domain; the model's choice complies.
    model = _ScriptedModelServer(
        "submit_ticket",
        {"ticket": "T-100", "domain": "domain_a"},
        "ticket submitted and verified",
        tool_turns=tool_turns,
    )
    await model.start()
    try:
        config = json.loads((profile_dir / "runner.json").read_text(encoding="utf-8"))
        config["model"] = {"backend": "endpoint", "endpoint": model.url}
        (profile_dir / "runner.json").write_text(json.dumps(config), encoding="utf-8")
        harness = _Harness(profile_dir, api)
        engine = harness.engine
        run_id, state = await engine.submit("app-reader", {"input": {"prompt": "file the ticket"}}, ("domain_a",))
        assert state is not None
        parked = await engine.wait_for(run_id)
        assert parked.status == "waiting_approval"
        assert api.calls == []  # the protected side effect stayed withheld
        assert [a.get("tool_calls") is not None for a in model.answers] == [True]

        task_ref = parked.task_ref
        record = harness.durable.store.get_task_state(task_ref)
        assert record.lifecycle_state is TaskLifecycleState.WAITING_APPROVAL
        wait = (record.extra or {}).get("wait") or {}
        # The controller park carries the accepted action's real arguments,
        # so the one-shot wake grant matches the committed decision.
        assert (wait.get("contract_ref") or {}).get("arguments") == {"ticket": "T-100", "domain": "domain_a"}

        receipt, reason = harness.durable.apply_wake(
            command_id=f"wake-{uuid.uuid4()}",
            kind="resume",
            issuer="app-reader",
            task_ref=task_ref,
            wait_token=wait["wait_token"],
            approval_ref="approval-ticket-1",
            keep_run_linkage=True,
        )
        assert reason is None and receipt.state.value == "applied"
        assert engine.resume(task_ref, harness.durable.store.run_for_task(task_ref), {}) is not None
        if tool_turns == 2:
            # Identical tool/arguments in a later decision require another
            # approval; the previous action's grant cannot authorize it.
            async with asyncio.timeout(10):
                while True:
                    candidate = engine.get_state(run_id)
                    if (
                        candidate is not None
                        and candidate.status == "waiting_approval"
                        and candidate.wait_token != wait["wait_token"]
                    ):
                        break
                    await asyncio.sleep(0.05)
            assert len(api.calls) == 1
            next_wait = harness.durable.store.get_task_state(task_ref).extra["wait"]
            assert next_wait["contract_ref"]["invocation_id"] != wait["contract_ref"]["invocation_id"]
            receipt, reason = harness.durable.apply_wake(
                command_id=f"wake-{uuid.uuid4()}",
                kind="resume",
                issuer="app-reader",
                task_ref=task_ref,
                wait_token=next_wait["wait_token"],
                approval_ref="approval-ticket-2",
                keep_run_linkage=True,
            )
            assert reason is None and receipt.state.value == "applied"
            assert engine.resume(task_ref, harness.durable.store.run_for_task(task_ref), {}) is not None
        # The resumed run replaces the parked state only once its coroutine
        # takes the serial slot; poll for a terminal status instead of
        # racing the stale parked entry.
        final = None
        for _ in range(200):
            candidate = engine.get_state(run_id)
            if candidate is not None and candidate.status in {"succeeded", "failed", "unknown"}:
                final = candidate
                break
            await asyncio.sleep(0.05)
        assert final is not None
        assert final.status == "succeeded"
        # Exactly one business call (the approved action); turn 1 replayed
        # from storage, so the model was asked once more (the final turn).
        assert (
            api.calls
            == [
                {
                    "tool": "submit_ticket",
                    "arguments": {"ticket": "T-100", "domain": "domain_a"},
                    "principal": "app-reader",
                }
            ]
            * tool_turns
        )
        assert [a["content"] for a in model.answers] == [""] * tool_turns + ["ticket submitted and verified"]
    finally:
        await model.stop()


def test_controller_binding_changes_the_frozen_execution_definition(tmp_path) -> None:
    """A running graph task cannot be reinterpreted as a Controller task."""
    directory = write_profile(
        tmp_path,
        durable={"database_url": f"sqlite:///{tmp_path / 'binding.db'}", "workspace": "ws-a"},
    )
    original = load_profile(directory).execution_definition_digest()
    path = directory / "runner.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    config["controller_loop"] = True
    path.write_text(json.dumps(config), encoding="utf-8")
    assert load_profile(directory).execution_definition_digest() != original


async def test_exhausted_budget_blocks_controller_model_request(loop_profile) -> None:
    """Controller calls obey the same budget gate as the graph's model node."""
    from hecate_governance.usage import BudgetGate
    from hecate_runner.budget import DurableBudgetLedger

    model = _ScriptedModelServer("query_inventory", {"domain": "domain_a", "sku": "A"}, "done")
    await model.start()
    try:
        path = loop_profile / "runner.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        config["model"] = {"backend": "endpoint", "endpoint": model.url}
        path.write_text(json.dumps(config), encoding="utf-8")
        api = _BusinessApi()
        harness = _Harness(loop_profile, api)
        ledger = DurableBudgetLedger(harness.durable.store.session_factory, limit=1)
        harness.engine._budget_gate = BudgetGate(ledger)
        run_id, state = await harness.engine.submit("app-reader", {"input": {"prompt": "check stock"}}, ("domain_a",))
        assert state is not None
        final = await harness.engine.wait_for(run_id)
        assert final.status == "failed"
        assert model.requests == []
        assert api.calls == []
        assert await ledger.remaining() == 1
    finally:
        await model.stop()


async def test_repeated_tool_calls_have_distinct_decision_scoped_ledger_entries(loop_profile) -> None:
    """Two uses of one tool are distinct actions, with exactly one ledger each."""
    model = _ScriptedModelServer("query_inventory", {"domain": "domain_a", "sku": "STOCK"}, "done", tool_turns=2)
    await model.start()
    try:
        path = loop_profile / "runner.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        config["model"] = {"backend": "endpoint", "endpoint": model.url}
        path.write_text(json.dumps(config), encoding="utf-8")
        api = _BusinessApi()
        harness = _Harness(loop_profile, api)
        run_id, _state = await harness.engine.submit("app-reader", {"prompt": "check both stocks"}, ("domain_a",))
        final = await harness.engine.wait_for(run_id)
        assert final.status == "succeeded", final.error
        assert [call["arguments"]["sku"] for call in api.calls] == ["STOCK-0", "STOCK-1"]
        assert len(harness.durable.store.list_run_actions(final.run_ref)) == 2
    finally:
        await model.stop()


@pytest.mark.parametrize("answer, status", [('{"answer": "verified"}', "succeeded"), ("not JSON", "failed")])
async def test_final_delivery_obeys_the_declared_schema(loop_profile, answer, status) -> None:
    """Runner delivery checks must reject the same invalid outputs as the platform."""
    model = _ScriptedModelServer("query_inventory", {"domain": "domain_a", "sku": "A"}, answer)
    await model.start()
    harness = None
    try:
        path = loop_profile / "runner.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        config["model"] = {"backend": "endpoint", "endpoint": model.url}
        path.write_text(json.dumps(config), encoding="utf-8")
        harness = _Harness(loop_profile, _BusinessApi())
        run_id, _state = await harness.engine.submit(
            "app-reader",
            {"prompt": "check stock", "output_schema": {"type": "object", "required": ["answer"]}},
            ("domain_a",),
        )
        final = await harness.engine.wait_for(run_id)
        assert final.status == status, final.error
        deliveries = [event for event in final.events if event.get("type") == "controller/final_answer"]
        assert deliveries[0]["text"] == answer
        assert bool(final.result_ref) is (status == "succeeded")
    finally:
        if harness is not None:
            await harness.engine.close()
            harness.durable.store.dispose()
        await model.stop()


@pytest.mark.parametrize(
    "schema", [{"$ref": "https://example.invalid/schema"}, {"$dynamicRef": "https://example.invalid/schema"}]
)
async def test_external_output_schema_is_rejected_before_submission(loop_profile, schema) -> None:
    """Validation never retrieves caller-selected schema URLs."""
    harness = _Harness(loop_profile, _BusinessApi())
    try:
        with pytest.raises(ValueError, match="invalid output_schema"):
            await harness.engine.submit("app-reader", {"prompt": "check", "output_schema": schema}, ("domain_a",))
        assert harness.durable.store.list_tasks() == []
    finally:
        await harness.engine.close()
        harness.durable.store.dispose()


@pytest.mark.parametrize("status, expected", [("business", "failed"), ("unknown", "reconciliation_required")])
async def test_failed_business_envelope_never_becomes_successful_feedback(loop_profile, status, expected) -> None:
    """Business refusals and indeterminate outcomes cannot drive a success answer."""

    class RefusingApi(_BusinessApi):
        """Return an explicit failure without throwing a transport exception."""

        async def __call__(self, name, arguments, principal, domains):
            self.calls.append(dict(arguments))
            return {"status": status, "detail": "business operation did not confirm success"}

    model = _ScriptedModelServer("query_inventory", {"domain": "domain_a", "sku": "A"}, "never asked")
    await model.start()
    harness = None
    try:
        path = loop_profile / "runner.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        config["model"] = {"backend": "endpoint", "endpoint": model.url}
        path.write_text(json.dumps(config), encoding="utf-8")
        api = RefusingApi()
        harness = _Harness(loop_profile, api)
        run_id, _state = await harness.engine.submit("app-reader", {"prompt": "check"}, ("domain_a",))
        final = await harness.engine.wait_for(run_id)
        assert final.status == expected, final.error
        assert len(api.calls) == 1
        assert len(model.requests) == 1
    finally:
        if harness is not None:
            await harness.engine.close()
            harness.durable.store.dispose()
        await model.stop()


async def test_malformed_model_tool_calls_cannot_be_downgraded_to_final_answer(tmp_path) -> None:
    """Malformed structured output cannot turn provider text into task success."""
    model = _ScriptedModelServer("query_inventory", {}, "unused")
    model.response_override = {"content": "claimed success", "tool_calls": "invalid"}
    await model.start()
    profile = write_profile(
        tmp_path,
        durable={"database_url": f"sqlite:///{tmp_path / 'malformed.db'}", "workspace": "ws-a"},
        controller_loop=True,
        model_backend={"backend": "endpoint", "endpoint": model.url},
    )
    api = _BusinessApi()
    harness = _Harness(profile, api)
    try:
        run_id, _state = await harness.engine.submit("app-reader", {"prompt": "check"}, ("domain_a",))
        final = await harness.engine.wait_for(run_id)
        assert final.status == "failed"
        assert api.calls == []
        assert final.error is not None
        assert "tool_calls must be an array" in final.error
    finally:
        await harness.engine.close()
        harness.durable.store.dispose()
        await model.stop()


async def test_protected_approval_checks_use_the_accepted_action_identity(tmp_path) -> None:
    """Managed approval lookup and consumption keep distinct turn identities."""
    from hecate_runner.managed import LeaseGate

    model = _ScriptedModelServer(
        "submit_inventory_update", {"domain": "domain_a", "sku": "A", "quantity": 1}, "done", tool_turns=2
    )
    await model.start()
    profile = write_profile(
        tmp_path,
        allowlist=["submit_inventory_update"],
        with_write_tool=True,
        durable={"database_url": f"sqlite:///{tmp_path / 'approval-identities.db'}", "workspace": "ws-a"},
        controller_loop=True,
        model_backend={"backend": "endpoint", "endpoint": model.url},
    )
    harness = _Harness(profile, _BusinessApi())
    requests: list[str] = []

    class StubScopeGate(LeaseGate):
        """The managed data scope permits these test requests."""

        def __init__(self) -> None:
            # This test exercises the approval path; no signed lease is used.
            pass

        def approval_scope_allows(self, domain):
            return domain == "domain_a"

    async def find_approval(request):
        requests.append(request.invocation_id)
        return f"approval:{request.invocation_id}"

    async def consume_approval(reference, request):
        return reference == f"approval:{request.invocation_id}"

    harness.engine._lease_gate = StubScopeGate()
    harness.engine._approval_finder = find_approval
    harness.engine._approval_gate = consume_approval
    try:
        run_id, state = await harness.engine.submit("app-reader", {"prompt": "update both"}, ("domain_a",))
        assert state is not None
        state.managed = True
        final = await harness.engine.wait_for(run_id)
        assert final.status == "succeeded", final.error
        actions = harness.durable.store.list_run_actions(final.run_ref)
        assert len(requests) == len(set(requests)) == len(actions) == 2
        assert set(requests) == {action["intent"]["action_key"] for action in actions}
    finally:
        await harness.engine.close()
        harness.durable.store.dispose()
        await model.stop()


class _RegionBusinessApi:
    """Asks for the region once, then serves the completed call."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, tool_name: str, arguments: dict, principal: str, domains: list[str]) -> dict:
        self.calls.append(dict(arguments))
        if "region" not in arguments:
            return {"status": "input_required", "contract": {"fields": ["region"]}}
        return {"status": "ok", "result": "region-ok"}


async def test_input_park_records_outcome_and_wake_continues_chain(tmp_path) -> None:
    """An input-required action parks claim-free; the wake's input reaches the model."""
    profile_dir = write_profile(
        tmp_path,
        durable={"database_url": f"sqlite:///{tmp_path / 'controller-input.db'}", "workspace": "ws-a"},
        controller_loop=True,
    )
    api = _RegionBusinessApi()

    answers: list[dict] = []

    class _RegionModel(_ScriptedModelServer):
        async def _serve(self, reader, writer):
            try:
                await reader.readline()
                headers: dict[str, str] = {}
                while True:
                    header = await reader.readline()
                    if header in (b"\r\n", b"\n", b""):
                        break
                    name, _, value = header.decode("latin-1").partition(":")
                    headers[name.strip().lower()] = value.strip()
                body = await reader.readexactly(int(headers.get("content-length", "0")))
                request = json.loads(body.decode("utf-8"))
                self.requests.append(request)
                messages = request.get("messages") or []
                tools = [m for m in messages if isinstance(m, dict) and m.get("role") == "tool"]
                provided = any(m.get("role") == "user" and "provided_input" in str(m.get("content")) for m in messages)
                if not tools:
                    answer = {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "c1",
                                "type": "function",
                                "function": {
                                    "name": "query_inventory",
                                    "arguments": json.dumps({"domain": "domain_a", "sku": "S-1"}),
                                },
                            }
                        ],
                    }
                elif provided and len(tools) == 1:
                    # The provided input is visible at the tail: the model
                    # completes the request WITH the human region.
                    answer = {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "c2",
                                "type": "function",
                                "function": {
                                    "name": "query_inventory",
                                    "arguments": json.dumps(
                                        {"domain": "domain_a", "sku": "S-1", "region": "FROM-PROVIDED"}
                                    ),
                                },
                            }
                        ],
                    }
                else:
                    answer = {"content": "region applied and verified"}
                answers.append(answer)
                payload = json.dumps(answer).encode("utf-8")
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    + f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
                    + payload
                )
                await writer.drain()
            except Exception:  # noqa: BLE001, S110 — connection-level failures end the exchange
                pass
            finally:
                writer.close()

    model = _RegionModel("query_inventory", {}, "unused")
    await model.start()
    try:
        config = json.loads((profile_dir / "runner.json").read_text(encoding="utf-8"))
        config["model"] = {"backend": "endpoint", "endpoint": model.url}
        (profile_dir / "runner.json").write_text(json.dumps(config), encoding="utf-8")
        harness = _Harness(profile_dir, api)
        engine = harness.engine
        run_id, state = await engine.submit("app-reader", {"input": {"prompt": "regional stock"}}, ("domain_a",))
        assert state is not None
        parked = await engine.wait_for(run_id)
        assert parked.status == "waiting_input"
        assert api.calls == [{"domain": "domain_a", "sku": "S-1"}]

        task_ref = parked.task_ref
        record = harness.durable.store.get_task_state(task_ref)
        assert record.lifecycle_state is TaskLifecycleState.WAITING_INPUT
        wait = (record.extra or {}).get("wait") or {}
        assert wait["wake_kind"] == "provide_input"
        # The input request was recorded as the action's REAL outcome — no
        # dangling claim to reconcile on the wake.
        assert len(harness.durable.store.list_run_actions(harness.durable.store.run_for_task(task_ref))) == 1

        receipt, reason = harness.durable.apply_wake(
            command_id=f"wake-{uuid.uuid4()}",
            kind="provide_input",
            issuer="app-reader",
            task_ref=task_ref,
            wait_token=wait["wait_token"],
            input_payload={"region": "EU"},
            keep_run_linkage=True,
        )
        assert reason is None and receipt.state.value == "applied"
        assert engine.resume(task_ref, harness.durable.store.run_for_task(task_ref), {}) is not None
        final = None
        for _ in range(200):
            candidate = engine.get_state(run_id)
            if candidate is not None and candidate.status in {"succeeded", "failed", "unknown"}:
                final = candidate
                break
            await asyncio.sleep(0.05)
        assert final is not None and final.status == "succeeded", final and final.error
        # The provided region reached the MODEL: its second tool call carries
        # it and the business served the completed request.
        assert api.calls == [
            {"domain": "domain_a", "sku": "S-1"},
            {"domain": "domain_a", "sku": "S-1", "region": "FROM-PROVIDED"},
        ]
        assert [answer.get("tool_calls") is not None for answer in answers] == [True, True, False]
        # Turn 1 replayed from storage on the wake: the model was asked
        # exactly twice fresh (region call + final answer).
        assert len(model.requests) == 3
    finally:
        await model.stop()
