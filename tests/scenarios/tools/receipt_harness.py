"""Harness binding the stub ticket service to the engine ToolWorker boundary.

S05/S07/S08 assert the G2 receipt/recovery semantics where they actually live:
the real ``ToolWorker`` against a real ``InMemoryEventStore``. Only the LLM
port (this file's ``ScenarioToolPort``) and the ticket backend (the existing
stub) are test doubles; receipt persistence, claim, digest conflict, and
recovery decisions all run for real.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from hecate.runtime.eventstore import InMemoryEventStore
from hecate.runtime.workers.tool_worker import ToolWorker
from tests.scenarios.tools.stub_ticket import StubTicketService

SCENARIO_TOOL = "create_test_ticket"


class ScenarioToolPort:
    """Minimal LLM-port double exposing only the tool-execution seam.

    ``raise_exc`` lets scenarios inject failures AFTER the ticket side
    effect has happened (crash windows, indeterminate transport errors).
    """

    def __init__(self, ticket: StubTicketService, raise_exc: Exception | None = None) -> None:
        self.ticket = ticket
        self.raise_exc = raise_exc

    async def tool_execute(self, name: str, args: dict[str, Any], context: dict | None = None) -> dict[str, Any]:
        result = await self.ticket.execute(name, args, context)
        if self.raise_exc is not None:
            raise self.raise_exc
        return result

    async def tool_execute_sandbox(
        self, name: str, args: dict[str, Any], context: dict | None = None
    ) -> dict[str, Any]:
        return await self.tool_execute(name, args, context)

    async def create_span(self, *args: Any, **kwargs: Any):
        class _CM:
            span_id = "span"

            async def __aenter__(self) -> ScenarioToolPort._CM:
                return self

            async def __aexit__(self, *exc: Any) -> None:
                return None

        return _CM()

    async def end_span(self, *args: Any, **kwargs: Any) -> None:
        return None


def scenario_payload(
    call_id: str,
    arguments: dict[str, Any],
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Engine-worker payload carrying one assistant tool call.

    ``history`` carries prior channel messages (e.g. the tool-result message
    from the original dispatch) — the real resume flow re-executes with the
    conversation history, and succeeded-result backfill reads it.
    """
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "file the ticket"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": SCENARIO_TOOL, "arguments": json.dumps(arguments)},
                }
            ],
        },
    ]
    messages.extend(history or [])
    return {"messages": messages}


def make_worker(store: InMemoryEventStore, port: ScenarioToolPort) -> ToolWorker:
    return ToolWorker(port=port, event_store=store)


def execution_context(session_id: uuid.UUID) -> dict[str, Any]:
    return {"session_id": session_id, "superstep": 0, "trace_id": None}
