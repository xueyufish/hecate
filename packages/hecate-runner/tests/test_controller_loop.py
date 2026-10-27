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


@pytest.mark.parametrize("failure", ["corrupt", "unavailable", "legacy", "legacy-with-new-revision"])
async def test_unrecoverable_input_history_parks_before_any_model_or_action(loop_profile, monkeypatch, failure) -> None:
    """Missing authority stops recovery without model calls or business effects."""
    model = _ScriptedModelServer("query_inventory", {"domain": "domain_a", "sku": "A"}, "done")
    await model.start()
    try:
        path = loop_profile / "runner.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        config["model"] = {"backend": "endpoint", "endpoint": model.url}
        path.write_text(json.dumps(config), encoding="utf-8")
        api = _BusinessApi()
        harness = _Harness(loop_profile, api)
        if failure in {"corrupt", "unavailable"}:

            def unreadable(*args):
                error = ValueError if failure == "corrupt" else RuntimeError
                raise error("history cannot be loaded")

            monkeypatch.setattr(harness.durable.store, "list_input_revisions", unreadable)
        elif failure == "legacy-with-new-revision":
            monkeypatch.setattr(
                harness.durable.store,
                "list_input_revisions",
                lambda *args: [{"revision_id": "later-input", "content": {"tier": "gold"}, "effective_turn": 1}],
            )
        run_input = {"input": {"prompt": "check stock"}}
        if failure.startswith("legacy"):
            run_input["provided"] = {"region": "EU"}
        run_id, state = await harness.engine.submit("app-reader", run_input, ("domain_a",))
        assert state is not None
        final = await harness.engine.wait_for(run_id)
        assert final.status == "reconciliation_required" and final.needs_reconciliation
        assert harness.durable.store.get_task_state(final.task_ref).lifecycle_state is (
            TaskLifecycleState.RECONCILIATION_REQUIRED
        )
        assert model.requests == [] and api.calls == []
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
            controller_loop=True,
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


class _TwoWakeBusinessApi:
    """Needs region first, then tier; both must ride real business calls."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, tool_name: str, arguments: dict, principal: str, domains: list[str]) -> dict:
        self.calls.append(dict(arguments))
        if "region" not in arguments:
            return {"status": "input_required", "contract": {"fields": ["region"]}}
        if "tier" not in arguments:
            return {"status": "input_required", "contract": {"fields": ["tier"]}}
        return {"status": "ok", "result": "complete"}


def _provided_indexes(messages: list[dict]) -> list[int]:
    return [
        index
        for index, message in enumerate(messages)
        if isinstance(message, dict)
        and message.get("role") == "user"
        and "provided_input" in str(message.get("content"))
    ]


def _tool_indexes(messages: list[dict]) -> list[int]:
    return [
        index for index, message in enumerate(messages) if isinstance(message, dict) and message.get("role") == "tool"
    ]


@pytest.mark.parametrize("with_prompt", [True, False], ids=["prompt", "structured-input"])
async def test_two_input_wakes_preserve_history_order_and_effects(tmp_path, with_prompt) -> None:
    """Two waits, two wakes: both values reach the model in causal order."""
    profile_dir = write_profile(
        tmp_path,
        durable={"database_url": f"sqlite:///{tmp_path / 'controller-two-wake.db'}", "workspace": "ws-a"},
        controller_loop=True,
    )
    api = _TwoWakeBusinessApi()

    class _TwoWakeModel(_ScriptedModelServer):
        def __init__(self) -> None:
            super().__init__("query_inventory", {}, "unused")

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
                provided = [
                    m
                    for m in messages
                    if isinstance(m, dict) and m.get("role") == "user" and "provided_input" in str(m.get("content"))
                ]
                if len(tools) == 0:
                    arguments: dict | None = {"domain": "domain_a", "sku": "S-2"}
                elif len(tools) == 1 and len(provided) == 1:
                    region = json.loads(provided[0]["content"])["provided_input"]["region"]
                    arguments = {"domain": "domain_a", "sku": "S-2", "region": region}
                elif len(tools) == 2 and len(provided) == 2:
                    arguments = {
                        "domain": "domain_a",
                        "sku": "S-2",
                        "region": json.loads(provided[0]["content"])["provided_input"]["region"],
                        "tier": json.loads(provided[1]["content"])["provided_input"]["tier"],
                    }
                else:
                    arguments = None
                if arguments is not None:
                    answer = {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": f"c{len(tools) + 1}",
                                "type": "function",
                                "function": {
                                    "name": "query_inventory",
                                    "arguments": json.dumps(arguments),
                                },
                            }
                        ],
                    }
                else:
                    answer = {"content": "region and tier applied"}
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

    model = _TwoWakeModel()
    await model.start()
    try:
        config = json.loads((profile_dir / "runner.json").read_text(encoding="utf-8"))
        config["model"] = {"backend": "endpoint", "endpoint": model.url}
        (profile_dir / "runner.json").write_text(json.dumps(config), encoding="utf-8")
        harness = _Harness(profile_dir, api)
        engine = harness.engine
        submitted_input = {"input": {"prompt" if with_prompt else "task": "regional tier stock"}}
        run_id, state = await engine.submit("app-reader", submitted_input, ("domain_a",))
        assert state is not None

        async def _terminal() -> object:
            async with asyncio.timeout(20):
                while True:
                    candidate = engine.get_state(run_id)
                    if candidate is not None and candidate.status in {"succeeded", "failed", "unknown"}:
                        return candidate
                    await asyncio.sleep(0.05)
            raise AssertionError("unreachable")

        async def _waiting(exclude_token: str) -> tuple[object, dict]:
            async with asyncio.timeout(20):
                while True:
                    candidate = engine.get_state(run_id)
                    if (
                        candidate is not None
                        and candidate.status == "waiting_input"
                        and candidate.wait_token
                        and candidate.wait_token != exclude_token
                    ):
                        break
                    await asyncio.sleep(0.05)
            task_ref = candidate.task_ref
            record = harness.durable.store.get_task_state(task_ref)
            wait = (record.extra or {}).get("wait") or {}
            return candidate, wait

        parked1, wait1 = await _waiting("")
        assert parked1.status == "waiting_input"
        task_ref = parked1.task_ref
        first_run = harness.durable.store.run_for_task(task_ref)

        wake1_id = f"wake-1-{uuid.uuid4()}"
        receipt, reason = harness.durable.apply_wake(
            command_id=wake1_id,
            kind="provide_input",
            issuer="app-reader",
            task_ref=task_ref,
            wait_token=wait1["wait_token"],
            input_payload={"region": "EU"},
            keep_run_linkage=True,
            controller_loop=True,
        )
        assert reason is None and receipt.state.value == "applied"
        # Duplicate command replay: idempotent, no second revision, no requeue.
        replay, replay_reason = harness.durable.apply_wake(
            command_id=wake1_id,
            kind="provide_input",
            issuer="app-reader",
            task_ref=task_ref,
            wait_token=wait1["wait_token"],
            input_payload={"region": "EU"},
            keep_run_linkage=True,
            controller_loop=True,
        )
        assert replay_reason is None and replay.state.value == "applied"
        assert len(harness.durable.store.list_input_revisions(task_ref)) == 1
        assert engine.resume(task_ref, harness.durable.store.run_for_task(task_ref), {}) is not None

        parked2, wait2 = await _waiting(wait1["wait_token"])
        assert parked2.status == "waiting_input"
        revisions = harness.durable.store.list_input_revisions(task_ref)
        assert [row["effective_turn"] for row in revisions] == [2]
        assert harness.durable.store.run_for_task(task_ref).id == first_run.id  # same run, no new attempt

        receipt2, reason2 = harness.durable.apply_wake(
            command_id=f"wake-2-{uuid.uuid4()}",
            kind="provide_input",
            issuer="app-reader",
            task_ref=task_ref,
            wait_token=wait2["wait_token"],
            input_payload={"tier": "gold"},
            keep_run_linkage=True,
            controller_loop=True,
        )
        assert reason2 is None and receipt2.state.value == "applied"
        assert engine.resume(task_ref, harness.durable.store.run_for_task(task_ref), {}) is not None

        final = await _terminal()
        assert final.status == "succeeded", final.error
        revisions = harness.durable.store.list_input_revisions(task_ref)
        assert [row["effective_turn"] for row in revisions] == [2, 3]
        assert [row["content"] for row in revisions] == [{"region": "EU"}, {"tier": "gold"}]
        assert harness.durable.store.run_for_task(task_ref).id == first_run.id
        # Every business call carried exactly the inputs accepted so far —
        # no call repeated, none executed with a value it could not have seen.
        assert api.calls == [
            {"domain": "domain_a", "sku": "S-2"},
            {"domain": "domain_a", "sku": "S-2", "region": "EU"},
            {"domain": "domain_a", "sku": "S-2", "region": "EU", "tier": "gold"},
        ]
        # The last model ask saw BOTH revisions in causal order: the first
        # input before the second turn's result, the second input after it.
        last_messages = model.requests[-1]["messages"]
        provided, tools = _provided_indexes(last_messages), _tool_indexes(last_messages)
        assert len(provided) == 2 and len(tools) == 3
        assert tools[0] < provided[0] < tools[1] < provided[1] < tools[2]
        assert len(model.requests) == 4  # each turn asked exactly once, replays asked nothing
    finally:
        await model.stop()


@pytest.mark.parametrize(
    "acceptance, match",
    [
        ({"version": 0}, "acceptance.version must be a positive integer"),
        ({"required_checks": [{"kind": "future"}]}, "unsupported acceptance check kind 'future'"),
        ({"required_checks": [{"kind": "content_contains"}]}, "content_contains check requires a nonempty value"),
    ],
)
async def test_invalid_acceptance_declaration_is_rejected_before_submission(loop_profile, acceptance, match) -> None:
    """A malformed standard admits no execution in the runner either."""
    harness = _Harness(loop_profile, _BusinessApi())
    try:
        with pytest.raises(ValueError, match=match):
            await harness.engine.submit("app-reader", {"prompt": "check", "acceptance": acceptance}, ("domain_a",))
        assert harness.durable.store.list_tasks() == []
    finally:
        await harness.engine.close()
        harness.durable.store.dispose()


_DECLARED = {
    "version": 3,
    "required_checks": [
        {"kind": "output_schema", "schema": {"type": "object", "required": ["answer"]}},
        {"kind": "content_contains", "value": "verified"},
    ],
}


async def _run_with_acceptance(profile_dir, model, acceptance: dict, prompt_extra: dict | None = None):
    await model.start()
    path = profile_dir / "runner.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    config["model"] = {"backend": "endpoint", "endpoint": model.url}
    path.write_text(json.dumps(config), encoding="utf-8")
    harness = _Harness(profile_dir, _BusinessApi())
    try:
        run_id, _state = await harness.engine.submit(
            "app-reader", {"prompt": "check", "acceptance": acceptance, **(prompt_extra or {})}, ("domain_a",)
        )
        final = await harness.engine.wait_for(run_id)
        return harness, final
    except BaseException:
        await harness.engine.close()
        harness.durable.store.dispose()
        await model.stop()
        raise


async def test_passing_acceptance_persists_the_versioned_record(loop_profile) -> None:
    """Success under a declared standard binds the full acceptance facts."""
    model = _ScriptedModelServer("query_inventory", {"domain": "domain_a", "sku": "A"}, '{"answer": "verified"}')
    harness, final = await _run_with_acceptance(loop_profile, model, _DECLARED)
    try:
        assert final.status == "succeeded", final.error
        record = harness.durable.store.get_task_state(final.task_ref)
        result = (record.extra or {})["result"]
        acceptance = result["acceptance"]
        assert acceptance["status"] == "passed"
        assert acceptance["version"] == 3
        assert acceptance["declaration_sha256"]
        assert acceptance["final_decision"]["decision_step_id"] == "agent-turn-000002"
        assert acceptance["final_decision"]["content_digest"]
        assert acceptance["task_ref"] == {"issuer": final.task_ref.issuer_domain, "id": final.task_ref.id}
        assert acceptance["run_ref"] == {"issuer": final.run_ref.issuer_domain, "id": final.run_ref.id}
        assert acceptance["evaluated_by"] == {"host": "runner", "principal": "app-reader"}
        assert {check["kind"]: check["status"] for check in acceptance["checks"]} == {
            "output_schema": "passed",
            "content_contains": "passed",
        }
        assert result["content"] == '{"answer": "verified"}'
        # The same acceptance facts ride the terminal governance event the
        # platform projection folds — one transaction, one event.
        terminal = [
            event
            for event in harness.durable.store.read_events(final.run_ref).events
            if event.payload.get("event_type") == "run_terminal"
        ]
        assert len(terminal) == 1
        assert terminal[0].payload["acceptance"] == acceptance
        assert terminal[0].payload["content"] == '{"answer": "verified"}'
    finally:
        await harness.engine.close()
        harness.durable.store.dispose()
        await model.stop()


async def test_schema_passes_but_required_content_fails_withholds_success(loop_profile) -> None:
    """Schema-passing delivery with a failed content check keeps content, not success."""
    model = _ScriptedModelServer("query_inventory", {"domain": "domain_a", "sku": "A"}, '{"answer": "not it"}')
    harness, final = await _run_with_acceptance(loop_profile, model, _DECLARED)
    try:
        assert final.status == "failed"
        assert final.error is not None and "verified" in final.error
        record = harness.durable.store.get_task_state(final.task_ref)
        result = (record.extra or {})["result"]
        assert result["error_category"] == "acceptance_failed"
        assert result["content"] == '{"answer": "not it"}'  # the delivery stays visible
        statuses = {check["kind"]: check["status"] for check in result["acceptance"]["checks"]}
        assert statuses == {"output_schema": "passed", "content_contains": "failed"}
        # The failed acceptance never redoes the confirmed business action:
        # one model turn, one governed call, one ledger entry.
        assert len(model.requests) == 2
        assert len(harness.durable.store.list_run_actions(final.run_ref)) == 1
    finally:
        await harness.engine.close()
        harness.durable.store.dispose()
        await model.stop()


async def test_undeclared_delivery_succeeds_but_records_not_declared(loop_profile) -> None:
    """A run without any declaration still succeeds — and says not_declared."""
    model = _ScriptedModelServer("query_inventory", {"domain": "domain_a", "sku": "A"}, "free-form answer")
    await model.start()
    harness = None
    try:
        path = loop_profile / "runner.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        config["model"] = {"backend": "endpoint", "endpoint": model.url}
        path.write_text(json.dumps(config), encoding="utf-8")
        harness = _Harness(loop_profile, _BusinessApi())
        run_id, _state = await harness.engine.submit("app-reader", {"prompt": "check"}, ("domain_a",))
        final = await harness.engine.wait_for(run_id)
        assert final.status == "succeeded", final.error
        result = (harness.durable.store.get_task_state(final.task_ref).extra or {})["result"]
        assert result["acceptance"]["status"] == "not_declared"
        assert result["acceptance"]["version"] is None
    finally:
        if harness is not None:
            await harness.engine.close()
            harness.durable.store.dispose()
        await model.stop()


async def test_expired_controller_owner_cannot_commit_acceptance(loop_profile, monkeypatch) -> None:
    """Taking ownership before the terminal commit fences the old evaluator."""
    finish = DurableRuntime.finish_run

    def take_over_before_finish(runtime, *args, **kwargs):
        handle = kwargs.get("lease")
        assert handle is not None
        runtime.store.leases.release(handle.lease_key, handle.holder, fencing_token=handle.fencing_token)
        assert runtime.store.leases.acquire(handle.lease_key, "replacement-owner", 120) is not None
        return finish(runtime, *args, **kwargs)

    monkeypatch.setattr(DurableRuntime, "finish_run", take_over_before_finish)
    model = _ScriptedModelServer("query_inventory", {"domain": "domain_a", "sku": "A"}, '{"answer":"verified"}')
    harness, final = await _run_with_acceptance(loop_profile, model, _DECLARED)
    try:
        assert final.status == "unknown"
        task = harness.durable.store.get_task_state(final.task_ref)
        assert task.lifecycle_state is TaskLifecycleState.RUNNING
        assert "result" not in task.extra
        assert all(
            event.payload.get("event_type") != "run_terminal"
            for event in harness.durable.store.read_events(final.run_ref).events
        )
        assert len(model.requests) == 2
    finally:
        await harness.engine.close()
        harness.durable.store.dispose()
        await model.stop()


@pytest.mark.parametrize("text", ['{"answer":"verified"}', '{"answer":"not it"}'])
async def test_delivery_result_survives_restart_and_is_readable_over_http(loop_profile, text) -> None:
    """Both successful and rejected deliveries remain visible in fresh host views."""
    from urllib.request import Request, urlopen

    from conftest_runner import READER_TOKEN
    from hecate_runner.server import RunnerServer

    model = _ScriptedModelServer("query_inventory", {"domain": "domain_a", "sku": "A"}, text)
    harness, final = await _run_with_acceptance(loop_profile, model, _DECLARED)
    expected = harness.durable.read_result(final.task_ref, final.run_ref)
    await harness.engine.close()
    harness.durable.store.dispose()
    fresh = _Harness(loop_profile, _BusinessApi())
    server = RunnerServer(fresh.profile, fresh.engine, fresh.evidence, durable=fresh.durable)
    http = server.serve()

    def fetch(formal):
        request = Request(f"http://127.0.0.1:{http.server_address[1]}/runs/{final.run_id}")
        request.add_header("Authorization", f"Bearer {READER_TOKEN}")
        if formal:
            request.add_header("X-Contract-Version", "0.1")
        with urlopen(request, timeout=10) as response:
            return json.load(response)

    try:
        legacy = await asyncio.to_thread(fetch, False)
        formal = await asyncio.to_thread(fetch, True)
        assert legacy["result"] == expected
        assert formal["detail_ns"]["result"] == expected
        assert expected["content"] == text
        assert model.requests and len(model.requests) == 2
    finally:
        server.close()
        await asyncio.to_thread(http.shutdown)
        http.server_close()
        fresh.durable.store.dispose()
        await model.stop()


@pytest.mark.parametrize("field", ["content", "standard", "decision", "namespace", "checks", "missing_history"])
async def test_corrupt_persisted_acceptance_never_reads_as_success(loop_profile, field) -> None:
    """A stored result must match its authoritative decision, scope and standard."""
    from hecate_durable.storage.models import TaskStateRow

    model = _ScriptedModelServer("query_inventory", {"domain": "domain_a", "sku": "A"}, '{"answer":"verified"}')
    harness, final = await _run_with_acceptance(loop_profile, model, _DECLARED)
    try:
        with harness.durable.store.session_factory() as session, session.begin():
            row = session.get(TaskStateRow, (final.task_ref.issuer_domain, final.task_ref.id))
            extra = json.loads(json.dumps(row.extra))
            result = extra["result"]
            if field == "content":
                result["content"] = "tampered"
            elif field == "standard":
                result["acceptance"]["declaration_sha256"] = "0" * 64
            elif field == "decision":
                result["acceptance"]["final_decision"]["content_digest"] = "0" * 64
            elif field == "namespace":
                result["acceptance"]["run_ref"]["issuer"] = "foreign"
            elif field == "missing_history":
                result.pop("acceptance")
            else:
                result["acceptance"]["checks"] = []
            row.extra = extra
        restored = harness.engine._durable_task_state(final.task_ref, final.principal, final.domains, final.run_ref)
        assert restored.status == "reconciliation_required" and restored.result is None
        assert len(model.requests) == 2
    finally:
        await harness.engine.close()
        harness.durable.store.dispose()
        await model.stop()


async def test_caller_mutation_cannot_replace_the_admitted_standard(loop_profile, monkeypatch) -> None:
    """The controller consumes the accepted SQL input instead of caller dictionaries."""
    run_controller = ExecutionEngine._run_controller

    async def mutate_caller_then_run(engine, state, run_id, run_input):
        run_input["acceptance"]["required_checks"][1]["value"] = "tampered"
        await run_controller(engine, state, run_id, run_input)

    monkeypatch.setattr(ExecutionEngine, "_run_controller", mutate_caller_then_run)
    model = _ScriptedModelServer("query_inventory", {"domain": "domain_a", "sku": "A"}, '{"answer":"verified"}')
    harness, final = await _run_with_acceptance(loop_profile, model, json.loads(json.dumps(_DECLARED)))
    try:
        assert final.status == "succeeded"
        assert harness.durable.read_result(final.task_ref, final.run_ref)["acceptance"]["status"] == "passed"
    finally:
        await harness.engine.close()
        harness.durable.store.dispose()
        await model.stop()


async def test_terminal_outcome_overrides_candidate_delivery_status(loop_profile) -> None:
    """A cancelled task cannot publish the candidate's earlier success label."""
    harness = _Harness(loop_profile, _BusinessApi())
    try:
        submission = harness.durable.submit(
            principal="app-reader", run_input={"prompt": "check"}, idempotency_key=None, domains=("domain_a",)
        )
        task_ref = submission.association.task_ref
        harness.durable.begin_run(task_ref)
        record = harness.durable.finish_run(
            task_ref, "cancelled", error="cancel took effect", result={"status": "succeeded", "content": "delivery"}
        )
        assert record.lifecycle_state is TaskLifecycleState.CANCELLED
        assert record.extra["result"]["status"] == "cancelled"
        events = harness.durable.store.read_events(submission.association.run_ref).events
        terminal = next(event for event in events if event.payload.get("event_type") == "run_terminal")
        assert terminal.payload["status"] == "cancelled"
    finally:
        await harness.engine.close()
        harness.durable.store.dispose()


async def test_checker_outage_after_admission_keeps_delivery_unverified(loop_profile, monkeypatch) -> None:
    """Restoring an admitted standard does not require its check implementation."""
    import hecate_runtime.delivery_acceptance as acceptance_module

    run_controller = ExecutionEngine._run_controller
    checker = acceptance_module._validate_check_strict

    def unavailable(*args):
        raise RuntimeError("checker unavailable after admission")

    async def lose_checker_then_run(engine, *args):
        monkeypatch.setattr(acceptance_module, "_validate_check_strict", unavailable)
        await run_controller(engine, *args)

    monkeypatch.setattr(ExecutionEngine, "_run_controller", lose_checker_then_run)
    model = _ScriptedModelServer("query_inventory", {"domain": "domain_a", "sku": "A"}, '{"answer":"verified"}')
    harness, final = await _run_with_acceptance(loop_profile, model, _DECLARED)
    try:
        assert final.status == "failed"
        monkeypatch.setattr(acceptance_module, "_validate_check_strict", checker)
        result = harness.durable.read_result(final.task_ref, final.run_ref)
        assert result["content"] == '{"answer":"verified"}'
        assert result["acceptance"]["status"] == "acceptance_unverified"
        assert len(model.requests) == 2
    finally:
        await harness.engine.close()
        harness.durable.store.dispose()
        await model.stop()


async def test_rewritten_standard_after_lost_terminal_commit_reconciles(loop_profile, monkeypatch) -> None:
    """A fresh Runner must stop a conflicting chain without another model request."""
    from hecate_durable.storage.models import TaskStateRow

    model = _ScriptedModelServer("query_inventory", {"domain": "domain_a", "sku": "A"}, '{"answer":"verified"}')

    def lost_terminal(*args, **kwargs):
        raise RuntimeError("terminal write lost after final decision")

    with monkeypatch.context() as patch:
        patch.setattr(DurableRuntime, "finish_run", lost_terminal)
        harness, final = await _run_with_acceptance(loop_profile, model, _DECLARED)
    fresh = None
    try:
        assert final.status == "unknown"
        await harness.engine.close()
        with harness.durable.store.session_factory() as session, session.begin():
            row = session.get(TaskStateRow, (final.task_ref.issuer_domain, final.task_ref.id))
            payload = json.loads(json.dumps(row.input_payload))
            payload["acceptance"]["required_checks"][1]["value"] = "rewritten"
            row.input_payload = payload
        fresh = _Harness(loop_profile, _BusinessApi())
        assert fresh.engine.resume(final.task_ref, final.run_ref, {}) == final.run_id
        recovered = await fresh.engine.wait_for(final.run_id)
        assert recovered.status == "reconciliation_required"
        assert recovered.result is None
        assert len(model.requests) == 2
    finally:
        if fresh is not None:
            await fresh.engine.close()
            fresh.durable.store.dispose()
        harness.durable.store.dispose()
        await model.stop()
