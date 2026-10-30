"""Bounded controlled continuation on the runner host.

Covers the runner slice of the continuation closure: an explicitly
declared ``controller.continuation`` policy lets the runner's delivery
gate persist one trigger fact per acceptance-rejected candidate and
re-enter the SAME durable decision chain with an internal feedback
message — the rejected answer and its committed decision stay
immutable, the confirmed business effect is never redone, and the
sustained-failure path stops with the real ``acceptance_failed``
classification. A config without a declared acceptance standard is
refused at admission, and the shared policy, feedback projection and
stop semantics are the same ones the platform applies.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from conftest_runner import write_profile
from hecate_runner.durable import DurableRuntime
from hecate_runner.engine import ExecutionEngine
from hecate_runner.evidence import EvidenceStore
from hecate_runner.profile import load_profile

_DECLARED = {
    "version": 3,
    "required_checks": [{"kind": "content_contains", "value": "verified"}],
}
_POLICY = {"version": 1, "max_continuations": 1}


class _ContinuationModelServer:
    """Scripted local model endpoint that answers the feedback correction.

    The first delivery fails the declared content check; once the
    persisted continuation feedback reaches the model input, the
    corrected answer is served. Every request is recorded so a test can
    assert the loop asked exactly once per turn.
    """

    def __init__(self, *, failing_text: str, corrected_text: str) -> None:
        self.failing_text = failing_text
        self.corrected_text = corrected_text
        self.requests: list[dict] = []
        self.feedback_payloads: list[dict] = []
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
            feedback = [
                json.loads(message["content"])["continuation_feedback"]
                for message in request.get("messages") or []
                if isinstance(message, dict)
                and isinstance(message.get("content"), str)
                and "continuation_feedback" in message["content"]
            ]
            if feedback:
                self.feedback_payloads.extend(feedback)
                answer = {"content": self.corrected_text}
            else:
                answer = {"content": self.failing_text}
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
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, tool_name: str, arguments: dict, principal: str, domains: list[str]) -> dict:
        self.calls.append({"tool": tool_name, "arguments": dict(arguments)})
        return {"status": "ok", "result": "done"}


@pytest.fixture(autouse=True)
def _shutdown_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNNER_SHUTDOWN_TOKEN", "shutdown-token-value")


@pytest.fixture
def loop_profile(tmp_path: Path) -> Path:
    return write_profile(
        tmp_path,
        durable={"database_url": f"sqlite:///{tmp_path / 'controller.db'}", "workspace": "ws-a"},
        controller_loop=True,
    )


def _open_durable_store(database_url: str):
    from hecate_durable.storage import SqlDurableStore

    store = SqlDurableStore(database_url)
    store.create_schema()
    return store


async def _run_with_continuation(
    profile_dir: Path,
    model,
    *,
    controller: dict,
    acceptance: dict | None = None,
    budget_limit: float | None = None,
):
    await model.start()
    path = profile_dir / "runner.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    config["model"] = {"backend": "endpoint", "endpoint": model.url}
    path.write_text(json.dumps(config), encoding="utf-8")
    profile = load_profile(profile_dir)
    store = _open_durable_store(config["durable"]["database_url"])
    harness = DurableRuntime(store, workspace=config["durable"]["workspace"])
    from hecate_governance.usage import BudgetGate
    from hecate_runner.budget import DurableBudgetLedger

    budget_gate = (
        BudgetGate(DurableBudgetLedger(store.session_factory, limit=budget_limit)) if budget_limit is not None else None
    )
    engine = ExecutionEngine(
        profile,
        EvidenceStore(profile.config.evidence_dir),
        _BusinessApi(),
        durable=harness,
        budget_gate=budget_gate,
    )
    try:
        run_id, _state = await engine.submit(
            "app-reader",
            {
                "prompt": "check the stock",
                "controller": controller,
                **({"acceptance": acceptance} if acceptance is not None else {}),
            },
            ("domain_a",),
        )
        final = await engine.wait_for(run_id)
        return engine, harness, final
    except BaseException:
        await engine.close()
        harness.store.dispose()
        await model.stop()
        raise


async def test_runner_continues_after_acceptance_failure_and_passes(loop_profile) -> None:
    """fail → persisted trigger → corrected delivery → succeeded."""
    model = _ContinuationModelServer(failing_text="a draft", corrected_text="now verified")
    engine, harness, final = await _run_with_continuation(
        loop_profile, model, controller={"continuation": _POLICY}, acceptance=_DECLARED
    )
    try:
        assert final.status == "succeeded", final.error
        record = harness.store.get_task_state(final.task_ref)
        result = (record.extra or {})["result"]
        assert result["content"] == "now verified"
        assert result["acceptance"]["status"] == "passed"
        # The feedback is the persisted internal control message, naming
        # the rejected candidate and the failed check.
        assert len(model.feedback_payloads) == 1
        feedback = model.feedback_payloads[0]
        assert feedback["attempt"] == 1 and feedback["max_continuations"] == 1
        assert feedback["candidate_decision_step_id"] == "agent-turn-000001"
        assert feedback["failed_checks"][0]["kind"] == "content_contains"
        # The durable trigger fact: one consumed attempt.
        block = record.extra["continuation"]
        assert block["max_continuations"] == 1
        assert [item["attempt"] for item in block["attempts"]] == [1]
        assert block["attempts"][0]["effective_turn"] == 2
        events = harness.store.read_events(final.run_ref).events
        assert sum(event.payload.get("event_type") == "task_continuation" for event in events) == 1
    finally:
        await engine.close()
        harness.store.dispose()
        await model.stop()


async def test_runner_sustained_failure_keeps_the_real_classification(loop_profile) -> None:
    """A correction that still fails stops with acceptance_failed."""
    model = _ContinuationModelServer(failing_text="a draft", corrected_text="another incomplete draft")
    engine, harness, final = await _run_with_continuation(
        loop_profile, model, controller={"continuation": _POLICY}, acceptance=_DECLARED
    )
    try:
        assert final.status == "failed"
        record = harness.store.get_task_state(final.task_ref)
        result = (record.extra or {})["result"]
        assert result["error_category"] == "acceptance_failed"
        assert result["content"] == "another incomplete draft"
        # Only the first candidate consumed a trigger.
        assert len(record.extra["continuation"]["attempts"]) == 1
    finally:
        await engine.close()
        harness.store.dispose()
        await model.stop()


async def test_runner_continuation_requires_a_declared_standard(loop_profile) -> None:
    """Corrections without a frozen standard are refused at admission."""
    model = _ContinuationModelServer(failing_text="a draft", corrected_text="now verified")
    await model.start()
    path = loop_profile / "runner.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    config["model"] = {"backend": "endpoint", "endpoint": model.url}
    path.write_text(json.dumps(config), encoding="utf-8")
    profile = load_profile(loop_profile)
    store = _open_durable_store(config["durable"]["database_url"])
    harness = DurableRuntime(store, workspace=config["durable"]["workspace"])
    engine = ExecutionEngine(profile, EvidenceStore(profile.config.evidence_dir), _BusinessApi(), durable=harness)
    try:
        with pytest.raises(ValueError, match="declared acceptance block"):
            await engine.submit(
                "app-reader",
                {"prompt": "check the stock", "controller": {"continuation": _POLICY}},
                ("domain_a",),
            )
        assert harness.store.list_tasks() == []
    finally:
        await engine.close()
        harness.store.dispose()
        await model.stop()


async def test_runner_malformed_continuation_declaration_is_refused(loop_profile) -> None:
    model = _ContinuationModelServer(failing_text="a draft", corrected_text="now verified")
    await model.start()
    path = loop_profile / "runner.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    config["model"] = {"backend": "endpoint", "endpoint": model.url}
    path.write_text(json.dumps(config), encoding="utf-8")
    profile = load_profile(loop_profile)
    store = _open_durable_store(config["durable"]["database_url"])
    harness = DurableRuntime(store, workspace=config["durable"]["workspace"])
    engine = ExecutionEngine(profile, EvidenceStore(profile.config.evidence_dir), _BusinessApi(), durable=harness)
    try:
        for bad in ({"max_continuations": True}, {"max_continuations": 0}, {"bound": 1}):
            with pytest.raises(ValueError):
                await engine.submit(
                    "app-reader",
                    {
                        "prompt": "check the stock",
                        "acceptance": _DECLARED,
                        "controller": {"continuation": bad},
                    },
                    ("domain_a",),
                )
        assert harness.store.list_tasks() == []
    finally:
        await engine.close()
        harness.store.dispose()
        await model.stop()


async def test_runner_without_continuation_keeps_the_prior_behavior(loop_profile) -> None:
    """An undeclared policy leaves the failed delivery exactly as it was."""
    model = _ContinuationModelServer(failing_text="a draft", corrected_text="now verified")
    engine, harness, final = await _run_with_continuation(loop_profile, model, controller={}, acceptance=_DECLARED)
    try:
        assert final.status == "failed"
        record = harness.store.get_task_state(final.task_ref)
        assert record.extra["result"]["error_category"] == "acceptance_failed"
        assert (record.extra or {}).get("continuation") is None
        assert model.feedback_payloads == []
        assert len(model.requests) == 1
    finally:
        await engine.close()
        harness.store.dispose()
        await model.stop()


async def test_runner_last_turn_retains_failed_delivery_without_trigger(loop_profile) -> None:
    model = _ContinuationModelServer(failing_text="last turn draft", corrected_text="unused verified")
    engine, harness, final = await _run_with_continuation(
        loop_profile, model, controller={"max_turns": 1, "continuation": _POLICY}, acceptance=_DECLARED
    )
    try:
        assert final.status == "failed"
        record = harness.store.get_task_state(final.task_ref)
        assert record.extra["result"]["content"] == "last turn draft"
        assert record.extra["result"]["error_category"] == "acceptance_failed"
        assert "continuation" not in record.extra
        assert len(model.requests) == 1
    finally:
        await engine.close()
        harness.store.dispose()
        await model.stop()


async def test_runner_continuation_uses_accumulated_model_budget(loop_profile) -> None:
    model = _ContinuationModelServer(failing_text="draft", corrected_text="unused verified")
    engine, harness, final = await _run_with_continuation(
        loop_profile,
        model,
        controller={"continuation": _POLICY},
        acceptance=_DECLARED,
        budget_limit=4096,
    )
    try:
        assert final.status == "failed"
        assert "budget" in final.error
        assert len(model.requests) == 1
        state = harness.store.get_task_state(final.task_ref)
        assert len(state.extra["continuation"]["attempts"]) == 1
        assert state.extra["result"]["content"] == "draft"
        assert state.extra["result"]["acceptance"]["status"] == "acceptance_failed"
        assert state.extra["result"]["error"] == final.error
        assert state.extra["result"]["error_category"] == "controller_execution_failed"
    finally:
        await engine.close()
        harness.store.dispose()
        await model.stop()
