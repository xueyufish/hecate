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
from tests.scenarios.tools.managed_platform import (
    ISSUER,
    SECRET,
    STEP6_POSTGRES_URL,
)


@pytest.fixture(
    params=[None, *([STEP6_POSTGRES_URL] if STEP6_POSTGRES_URL else [])],
    ids=["sqlite", "postgres"][: 1 + bool(STEP6_POSTGRES_URL)],
)
def step6_runner_database_url(request):
    """Run the same installed-host scenarios on SQLite and optional PostgreSQL.

    Each PostgreSQL-parametrized test gets its OWN schema on the shared
    database (search_path via URL options): the scenario assertions count
    durable rows (e.g. "exactly one local Task after a lost accept"), so
    tests must not see each other's tasks. The DDL runs off-thread because
    a sync psycopg connect must never execute on the event-loop thread
    (Windows hang, see tests/test_execution/conftest.py).
    """

    if request.param is None:
        yield None
        return

    import uuid
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy import create_engine, text

    schema = f"s_{uuid.uuid4().hex[:12]}"
    engine = create_engine(request.param)

    def _ddl(statement: str) -> None:
        with engine.connect() as conn:
            conn.execute(text(statement))
            conn.commit()

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(_ddl, f'CREATE SCHEMA "{schema}"').result(timeout=30)
    try:
        separator = "&" if "?" in request.param else "?"
        yield f"{request.param}{separator}options=-csearch_path%3D{schema}%2Cpublic"
    finally:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(_ddl, f'DROP SCHEMA IF EXISTS "{schema}" CASCADE').result(timeout=30)
        engine.dispose()


@pytest.fixture
def managed_secrets():
    from hecate.core.composition.managed_secrets import (
        clear_managed_secrets,
        register_managed_secret,
    )

    register_managed_secret(ISSUER, SECRET)
    yield
    clear_managed_secrets()


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


class StubToolCallDelta(SimpleNamespace):
    """Provider-shaped streamed tool call with OpenAI serialization."""

    def model_dump(self) -> dict[str, Any]:
        """Serialize the same complete call consumed by the direct loop."""
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.function.name,
                "arguments": self.function.arguments,
            },
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
                    response = await responder(messages)
                    deltas = [
                        StubToolCallDelta(
                            index=index, id=tc["id"], type="function", function=SimpleNamespace(**tc["function"])
                        )
                        for index, tc in enumerate(response.tool_calls or [])
                    ]
                    yield {"content": response.content, "tool_calls": deltas}
                    yield {"content": "", "usage": response.usage}
                    yield {"content": "", "finish_reason": response.finish_reason}
                    return
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


def feedback_driven_responder(
    *,
    read_tool: str,
    material_query: dict[str, Any],
    material_answer: str,
    followup_tool: str,
    followup_args_from_material: Callable[[str], dict[str, Any]],
    final_content_from: Callable[[str, str], str],
    structured: bool = False,
) -> tuple[Responder, dict[str, int]]:
    """Multi-turn feedback-driven responder (step5e).

    Round 1 proposes a read of the material; round 2 INSPECTS the tool
    result content and derives the follow-up write arguments from it; the
    final round produces the structured answer from both results. Returns
    the responder and a round-counter dict the scenario can assert on
    (deterministic; no model involved).
    """
    from tests.scenarios.conftest import llm_response, tool_call

    counters = {"reads": 0, "writes": 0, "finals": 0, "rounds": 0}
    material_content = ""

    async def responder(messages: list[dict[str, Any]]) -> SimpleNamespace:
        nonlocal material_content
        counters["rounds"] += 1
        last = messages[-1] if messages else {}
        if last.get("role") == "tool":
            content = last.get("content", "")
            if counters["writes"] == 0 and counters["reads"] > 0 and "status" in content:
                # PG serializes dict tool results with repr; DL uses JSON.
                import ast

                try:
                    material = json.loads(content)
                except json.JSONDecodeError:
                    material = ast.literal_eval(content)
                material_content = json.dumps(material)
                counters["writes"] += 1
                return llm_response(
                    tool_calls=[
                        tool_call(
                            f"call_followup_{counters['writes']}",
                            followup_tool,
                            followup_args_from_material(material_content),
                        )
                    ]
                )
            counters["finals"] += 1
            answer = final_content_from(material_content, content)
            if structured:
                answer = json.dumps({"answer": answer})
            return llm_response(content=answer)
        counters["reads"] += 1
        return llm_response(tool_calls=[tool_call("call_read_1", read_tool, material_query)])

    return responder, counters
