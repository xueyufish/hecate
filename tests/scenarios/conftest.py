"""Fixtures for the platform evolution scenario pack.

Wires the hermetic Tier-1 harness shared by all scenarios:

- a seeded agent + custom ticket tool (DB rows in the shared in-memory engine),
- the stub ticket service bound at the ``ToolRegistry`` boundary (policy,
  approval, and registry code paths above it run for real),
- a scripted LLM service patched at the chat API boundary,
- a per-test ``InMemoryEventStore`` exposed through the ``get_event_store``
  dependency so evidence assertions can read durable events.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.agent import AgentModel
from hecate.models.tool import ToolModel
from hecate.models.tool_policy import ToolPolicyRuleModel
from hecate.runtime.eventstore import InMemoryEventStore

SCENARIO_TOOL = "create_test_ticket"
SCENARIO_MODEL = "gpt-4o"

TICKET_TOOL_PARAMETERS = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Ticket title"},
        "body": {"type": "string", "description": "Ticket body"},
    },
    "required": ["title", "body"],
}


def llm_response(
    content: str = "",
    tool_calls: list[dict[str, Any]] | None = None,
) -> SimpleNamespace:
    """Build a minimal LLMResponse-shaped object for the scripted service."""
    return SimpleNamespace(
        content=content,
        model=SCENARIO_MODEL,
        tool_calls=tool_calls,
        finish_reason="tool_calls" if tool_calls else "stop",
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    )


def tool_call(call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """OpenAI-format tool call as produced by a chat completion response."""
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


Responder = Callable[[list[dict[str, Any]]], Awaitable[SimpleNamespace]]


@pytest.fixture
def scripted_llm(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Patch the chat module's LLM service with a scenario-owned responder.

    The responder receives the current message list and returns the next
    LLMResponse; tool results arrive as messages with ``role == "tool"``.
    An optional ``stream_responder`` (async generator over chunk dicts with
    ``content`` / ``finish_reason`` keys) backs ``chat_stream``.
    """

    def _install(responder: Responder, stream_responder: Callable[..., Any] | None = None) -> None:
        class _ScriptedLLMService:
            async def chat(self, *, messages: list[dict[str, Any]], **kwargs: Any) -> SimpleNamespace:
                return await responder(messages)

            async def chat_stream(self, *, messages: list[dict[str, Any]], **kwargs: Any):
                if stream_responder is None:
                    raise RuntimeError("no stream responder installed for this scenario")
                async for chunk in stream_responder(messages):
                    yield chunk

        monkeypatch.setattr("hecate.channel.api.v1.chat.llm_service", _ScriptedLLMService())

    return _install


def ticket_loop_responder(
    ticket_args: dict[str, Any],
    final_content: str,
    call_id: str = "call_scenario_001",
) -> Responder:
    """Script a one-tool-call loop: propose the ticket write, then finalize."""
    from tests.scenarios.conftest import llm_response, tool_call

    async def responder(messages: list[dict[str, Any]]) -> SimpleNamespace:
        if messages and messages[-1].get("role") == "tool":
            return llm_response(content=final_content)
        return llm_response(tool_calls=[tool_call(call_id, SCENARIO_TOOL, ticket_args)])

    return responder


@pytest.fixture
def ticket_service(monkeypatch: pytest.MonkeyPatch):
    """Bind the stub ticket service at the ToolRegistry boundary.

    Only the scenario tool name is intercepted; every other tool keeps the
    real registry routing. The stub records invocations so scenarios can
    assert side-effect counts and arguments deterministically.
    """
    from hecate.tools.tool.registry import ToolRegistry
    from tests.scenarios.tools.stub_ticket import StubTicketService

    stub = StubTicketService(tool_name=SCENARIO_TOOL)
    original_execute = ToolRegistry.execute

    async def patched_execute(self: ToolRegistry, name: str, args: dict[str, Any], context: dict | None = None):
        if name == stub.tool_name:
            return await stub.execute(name, args, context)
        return await original_execute(self, name, args, context)

    monkeypatch.setattr(ToolRegistry, "execute", patched_execute)
    return stub


@pytest_asyncio.fixture
async def scenario_agent(db_session: AsyncSession, default_workspace) -> AgentModel:
    """Seed a chat agent configured with the scenario ticket tool."""
    tool = ToolModel(
        workspace_id=default_workspace.id,
        name=SCENARIO_TOOL,
        description="Create a ticket in the test ticket service",
        source="custom",
        parameters=TICKET_TOOL_PARAMETERS,
    )
    agent = AgentModel(
        workspace_id=default_workspace.id,
        name="Scenario Agent",
        model_config_db={"model": SCENARIO_MODEL},
        mode="chat",
        tools=[SCENARIO_TOOL],
    )
    db_session.add_all([tool, agent])
    await db_session.flush()
    return agent


@pytest_asyncio.fixture
async def add_policy_rule(db_session: AsyncSession, default_workspace):
    """Return a coroutine factory seeding ToolPolicyRuleModel rows."""

    async def _add(
        action: str,
        tool_pattern: str = SCENARIO_TOOL,
        priority: int = 10,
    ) -> ToolPolicyRuleModel:
        rule = ToolPolicyRuleModel(
            workspace_id=default_workspace.id,
            agent_id=None,
            tool_pattern=tool_pattern,
            action=action,
            priority=priority,
            arg_conditions={},
        )
        db_session.add(rule)
        await db_session.flush()
        return rule

    return _add


@pytest_asyncio.fixture
async def scenario_event_store():
    """Per-test event store exposed to the app via the get_event_store override."""
    return InMemoryEventStore()


@pytest_asyncio.fixture
async def scenario_client(client, scenario_event_store: InMemoryEventStore):
    """The shared HTTP client with the event store dependency redirected."""
    from hecate.core.deps_event_store import get_event_store
    from hecate.main import app

    app.dependency_overrides[get_event_store] = lambda: scenario_event_store
    yield client
    app.dependency_overrides.pop(get_event_store, None)


@pytest_asyncio.fixture
async def scenario_error_client(client):
    """HTTP client variant that surfaces the app's 500 envelope instead of
    re-raising the server exception (Starlette's ServerErrorMiddleware
    re-raises after producing the response; ASGITransport propagates that
    unless ``raise_app_exceptions=False``)."""
    from httpx import ASGITransport, AsyncClient

    from hecate.main import app

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def grant_approvals(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate a human approver on the documented ``_inner_backend`` seam.

    The fail-closed callback itself (once-only consumption, scope caching,
    audit-pair emission) still runs for real; only the answerer is scripted.
    """
    from hecate.runtime.security.approval import FailingClosedApprovalCallback
    from hecate.runtime.tool_access import ApprovalDecision, ApprovalScope

    async def _approving_inner(self, tool_name, arguments, risk_level, context):
        return ApprovalDecision(approved=True, reason="scenario_human_approved", scope=ApprovalScope.ONCE)

    monkeypatch.setattr(FailingClosedApprovalCallback, "_delegate_inner", _approving_inner)


def agent_chat_url(agent: AgentModel) -> str:
    return f"/v1/agents/{agent.id}/chat/completions"


def chat_body(
    content: str,
    session_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": SCENARIO_MODEL,
        "messages": [{"role": "user", "content": content}],
    }
    if session_id is not None:
        body["session_id"] = str(session_id)
    return body
