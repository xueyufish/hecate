"""Actual and deterministic model recovery through the same TCP entry.

Real observations are opt-in and recorded separately. PostgreSQL persists
ORM facts, events and snapshots; SQLite certifies in-process recovery only.
"""

# ruff: noqa: F811
# Pytest resolves the re-exported acceptance fixtures by parameter name.

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from tests.scenarios.baseline.run_autonomous_baseline import (
    BASELINE_DIR,
    RecordedModelService,
    StubBusinessRegistry,
    provider_credentials_available,
)
from tests.test_channel.test_g3_real_tcp_disconnect import (
    PG_URL,
    _assert_terminal_evidence,
    _RawSseClient,
    entry_stack,  # noqa: F401 - shared acceptance fixture
    real_server,  # noqa: F401 - shared acceptance fixture
)
from tests.test_channel.test_g3_real_tcp_disconnect import (
    gated_provider as original_provider,  # noqa: F401 - shared acceptance fixture
)

_CONTINUE = "Continue: what was the previous calculation result? Return JSON with an answer field."


@pytest.fixture
def provider_source():
    """Default to a deterministic provider; real observations opt in."""
    return "synthetic"


@pytest.fixture
def gated_provider(original_provider, provider_source, monkeypatch):
    """Park after a succeeded tool, before the next model invocation."""
    provider, executor = original_provider
    model = os.environ.get("BASELINE_MODEL", "gpt-4o")
    if provider_source == "real_model" and (
        os.environ.get("HECATE_RUN_REAL_MODEL_BASELINE") != "1" or not provider_credentials_available(model)
    ):
        pytest.skip(
            "real recovery requires explicit HECATE_RUN_REAL_MODEL_BASELINE=1 and selected-provider credentials"
        )
    provider.inputs = []
    provider.hold_answer.set()
    recorder = RecordedModelService(model, StubBusinessRegistry("normal"))
    provider.recorder = recorder

    async def chat(**kwargs):
        messages = kwargs["messages"]
        receipts = [m for m in messages if m.get("role") == "tool"]
        provider.inputs.append([dict(m) for m in messages])
        if receipts and provider.hold_answer.is_set():
            provider.answer_entered.set()
            while not provider.release_answer.is_set():
                await asyncio.sleep(0.02)
        if provider_source == "real_model":
            return await recorder.chat(**kwargs)
        continuing = "Continue" in str(messages[-1].get("content", ""))
        calls = (
            []
            if continuing or receipts
            else [
                {
                    "id": "calculation",
                    "type": "function",
                    "function": {"name": "stub_calculator", "arguments": '{"a":2,"b":3}'},
                }
            ]
        )
        return SimpleNamespace(
            content=json.dumps({"answer": receipts[-1]["content"] if receipts else "missing context"})
            if not calls
            else "",
            tool_calls=calls,
            finish_reason="tool_calls" if calls else "stop",
            model=model,
            usage={},
        )

    async def chat_stream(**kwargs):
        response = await chat(**kwargs)
        yield {
            "content": response.content,
            "tool_calls": [
                SimpleNamespace(index=i, id=call["id"], type="function", function=SimpleNamespace(**call["function"]))
                for i, call in enumerate(response.tool_calls or [])
            ],
            "finish_reason": response.finish_reason,
        }
        yield {"content": "", "tool_calls": None, "usage": response.usage}

    monkeypatch.setattr(provider, "chat", chat)
    monkeypatch.setattr(provider, "chat_stream", chat_stream)
    return provider, executor


async def _exercise_recovery(real_server, entry_stack, gated_provider, request, provider_source):
    provider, executor = gated_provider
    observation = {
        "source": provider_source,
        "model": provider.recorder.model,
        "gate": False,
        "storage": "postgres" if PG_URL else "sqlite_orm_memory_events_and_snapshots",
        "status": "failed",
        "cost_amount": None,
        "price_version": "unverified",
    }
    try:
        await _exercise_recovery_inner(real_server, entry_stack, gated_provider, request, observation)
        observation["status"] = "passed"
    except BaseException as exc:
        observation["error_type"] = type(exc).__name__
        raise
    finally:
        if provider_source == "real_model":
            observation.update(
                {"model_inputs": provider.inputs, "rounds": provider.recorder.rounds, "tool_calls": executor.calls}
            )
            out = BASELINE_DIR / (
                "real_model_disconnect_v1_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + ".json"
            )
            out.write_text(json.dumps(observation, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


async def _exercise_recovery_inner(real_server, entry_stack, gated_provider, request, observation):
    import uuid

    from hecate.core.composition import entry_assembly
    from hecate.main import app
    from hecate.runtime.eventstore import EventType

    provider, executor = gated_provider
    session_id = uuid.uuid4()
    raw = _RawSseClient(
        real_server.base_url,
        f"/v1/agents/{real_server.agent_id}/chat/completions",
        {
            "messages": [
                {"role": "user", "content": "Use stub_calculator to add 2 and 3. Return JSON with an answer field."}
            ],
            "session_id": str(session_id),
            "stream": True,
        },
    )
    request.addfinalizer(raw.rst_close)
    observation["correlation"] = raw.correlation
    async with asyncio.timeout(90):
        while not provider.answer_entered.is_set():
            await asyncio.sleep(0.02)
    assert len(executor.calls) == 1
    raw.rst_close()
    provider.release_answer.set()
    await _assert_terminal_evidence(entry_stack, raw.correlation, "cancelled")
    observation["cancelled_terminal_verified"] = True
    events = await entry_stack.reader_store.get_events(session_id)
    succeeded = [e for e in events if e.event_type is EventType.TOOL_RESULT and e.payload.get("status") == "succeeded"]
    assert len(succeeded) == 1
    if PG_URL:
        from hecate.studio.event_state.postgres_store import PostgresEventStore
        from hecate.studio.session_state.postgres_store import PostgresSessionStateStore

        app.state.event_store = PostgresEventStore(entry_stack.server_factory)
        app.state.session_state_store = PostgresSessionStateStore(entry_stack.server_factory)
        entry_assembly.register_shared_event_store(app.state.event_store)
        entry_assembly.register_shared_session_state_store(app.state.session_state_store)

    provider.hold_answer.clear()
    first_recovery_input = len(provider.inputs)
    async with httpx.AsyncClient(base_url=real_server.base_url, timeout=100) as client:
        recovery = await client.post(
            f"/v1/agents/{real_server.agent_id}/chat/completions",
            json={
                "messages": [
                    {
                        "role": "user",
                        "content": _CONTINUE,
                    }
                ],
                "session_id": str(session_id),
                "output_schema": {
                    "type": "object",
                    "required": ["answer"],
                    "properties": {"answer": {"type": "string"}},
                },
            },
        )
    observation.update({"response_status": recovery.status_code, "recovery_response": recovery.json()})
    report = observation
    assert recovery.status_code == 200, report
    assert len(executor.calls) == 1, report
    receipts = [m for m in provider.inputs[first_recovery_input] if m.get("role") == "tool"]
    assert len(receipts) == 1 and receipts[0]["content"] == "5", report
    observation["receipt_in_first_recovery_input"] = True
    document = json.loads(recovery.json()["choices"][0]["message"]["content"])
    assert "5" in document["answer"], report


async def test_deterministic_disconnect_restores_prior_context(
    real_server, entry_stack, gated_provider, request, provider_source
):
    await _exercise_recovery(real_server, entry_stack, gated_provider, request, provider_source)


@pytest.mark.parametrize("provider_source", ["real_model"])
async def test_real_model_disconnect_recovery_zero_duplicate(
    real_server, entry_stack, gated_provider, request, provider_source
):
    await _exercise_recovery(real_server, entry_stack, gated_provider, request, provider_source)
