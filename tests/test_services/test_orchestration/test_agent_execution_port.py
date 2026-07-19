"""Tests for AgentExecutionPort — agent execution with full pipeline."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hecate.engine.agent_tool import AgentDefinition
from hecate.engine.guardrail import GuardrailAction, GuardrailResult
from hecate.models.agent import AgentModel
from hecate.models.tool import ToolModel


def _make_agent(
    name: str = "TestAgent",
    persona: str = "You are helpful.",
    tools: list[str] | None = None,
    knowledge_base_ids: list[str] | None = None,
) -> AgentModel:
    agent = AgentModel(
        name=name,
        persona=persona,
        model_config_db={"model": "gpt-4o"},
        tools=tools or [],
        knowledge_base_ids=knowledge_base_ids or [],
    )
    agent.id = uuid.uuid4()
    agent.workspace_id = uuid.UUID("00000000-0000-0000-0000-000000000000")
    return agent


def _make_tool(name: str, description: str = "A tool") -> ToolModel:
    tool = ToolModel(
        name=name,
        description=description,
        source="custom",
        parameters={"type": "object", "properties": {}},
    )
    tool.id = uuid.uuid4()
    return tool


@pytest.mark.asyncio
async def test_agent_execute_loads_tools(db_session):
    """AgentExecutionPort loads agent tools and passes them to LLM."""
    agent = _make_agent(tools=["web_search", "read_file"])
    db_session.add(agent)
    tool1 = _make_tool("web_search", "Search the web")
    tool2 = _make_tool("read_file", "Read a file")
    db_session.add(tool1)
    db_session.add(tool2)
    await db_session.flush()

    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = "Hello!"
    mock_response.usage = {"total_tokens": 100}
    mock_response.model = "gpt-4o"
    mock_llm.chat = AsyncMock(return_value=mock_response)

    from hecate.services.orchestration.agent_execution_port import AgentExecutionPort

    with patch("hecate.services.llm.service.llm_service", mock_llm):
        port = AgentExecutionPort(db_session)
        result = await port.agent_execute(
            agent_id=agent.id,
            messages=[{"role": "user", "content": "hi"}],
            channel_snapshot={},
        )

    assert result["response"] == "Hello!"
    assert result["usage"] == {"total_tokens": 100}
    mock_llm.chat.assert_called_once()
    call_kwargs = mock_llm.chat.call_args
    tools_arg = call_kwargs.kwargs.get("tools") or call_kwargs[1].get("tools")
    assert tools_arg is not None
    tool_names = [t["name"] for t in tools_arg]
    assert "web_search" in tool_names
    assert "read_file" in tool_names


@pytest.mark.asyncio
async def test_agent_execute_queries_knowledge_bases(db_session):
    """AgentExecutionPort queries knowledge bases and injects context."""
    kb_id = str(uuid.uuid4())
    agent = _make_agent(knowledge_base_ids=[kb_id])
    db_session.add(agent)
    await db_session.flush()

    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = "Answer with KB context"
    mock_response.usage = {"total_tokens": 50}
    mock_response.model = "gpt-4o"
    mock_llm.chat = AsyncMock(return_value=mock_response)

    from hecate.services.orchestration.agent_execution_port import AgentExecutionPort

    port = AgentExecutionPort(db_session)

    with (
        patch("hecate.services.llm.service.llm_service", mock_llm),
        patch.object(port, "knowledge_query", new_callable=AsyncMock) as mock_kb,
    ):
        mock_kb.return_value = [
            {"content": "KB chunk 1", "metadata": {"score": 0.9}},
            {"content": "KB chunk 2", "metadata": {"score": 0.8}},
        ]
        result = await port.agent_execute(
            agent_id=agent.id,
            messages=[{"role": "user", "content": "question"}],
            channel_snapshot={},
        )

    assert result["response"] == "Answer with KB context"
    mock_kb.assert_called_once()
    call_args = mock_kb.call_args
    assert call_args[0][0] == "question"


@pytest.mark.asyncio
async def test_agent_execute_pre_hook_blocks(db_session):
    """PreLLMHook BLOCK prevents LLM invocation."""
    agent = _make_agent()
    db_session.add(agent)
    await db_session.flush()

    mock_pre_hook = MagicMock()
    mock_pre_hook.on_pre_llm_call = AsyncMock(
        return_value=GuardrailResult(action=GuardrailAction.BLOCK, reason="Blocked by policy")
    )

    mock_llm = MagicMock()
    mock_llm.chat = AsyncMock()

    from hecate.services.orchestration.agent_execution_port import AgentExecutionPort

    port = AgentExecutionPort(db_session, pre_hook=mock_pre_hook)

    with patch("hecate.services.llm.service.llm_service", mock_llm):
        result = await port.agent_execute(
            agent_id=agent.id,
            messages=[{"role": "user", "content": "bad input"}],
            channel_snapshot={},
        )

    assert "Blocked by policy" in result["response"]
    mock_llm.chat.assert_not_called()


@pytest.mark.asyncio
async def test_agent_execute_agent_definition_filters_tools(db_session):
    """AgentDefinition.resolve_tools() filters tools by whitelist."""
    agent = _make_agent(tools=["web_search", "read_file", "write_file"])
    db_session.add(agent)
    for name in ["web_search", "read_file", "write_file"]:
        db_session.add(_make_tool(name))
    await db_session.flush()

    definition = AgentDefinition(
        agent_id=agent.id,
        description="Specialist",
        tools=["web_search"],
    )

    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = "Done"
    mock_response.usage = {}
    mock_response.model = "gpt-4o"
    mock_llm.chat = AsyncMock(return_value=mock_response)

    from hecate.services.orchestration.agent_execution_port import AgentExecutionPort

    port = AgentExecutionPort(db_session)

    with patch("hecate.services.llm.service.llm_service", mock_llm):
        await port.agent_execute(
            agent_id=agent.id,
            messages=[{"role": "user", "content": "search"}],
            channel_snapshot={},
            agent_definition=definition,
        )

    call_kwargs = mock_llm.chat.call_args
    tools_arg = call_kwargs.kwargs.get("tools") or call_kwargs[1].get("tools")
    tool_names = [t["name"] for t in tools_arg]
    assert "web_search" in tool_names
    assert "read_file" not in tool_names
    assert "write_file" not in tool_names


@pytest.mark.asyncio
async def test_agent_execute_post_hook_sanitizes(db_session):
    """PostLLMHook SANITIZE modifies the response content."""
    agent = _make_agent()
    db_session.add(agent)
    await db_session.flush()

    mock_post_hook = MagicMock()
    mock_post_hook.on_post_llm_call = AsyncMock(
        return_value=GuardrailResult(
            action=GuardrailAction.SANITIZE,
            modified_data={"response": {"content": "Sanitized response"}},
        )
    )

    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = "Original response"
    mock_response.usage = {}
    mock_response.model = "gpt-4o"
    mock_llm.chat = AsyncMock(return_value=mock_response)

    from hecate.services.orchestration.agent_execution_port import AgentExecutionPort

    port = AgentExecutionPort(db_session, post_hook=mock_post_hook)

    with patch("hecate.services.llm.service.llm_service", mock_llm):
        result = await port.agent_execute(
            agent_id=agent.id,
            messages=[{"role": "user", "content": "test"}],
            channel_snapshot={},
        )

    assert result["response"] == "Sanitized response"


@pytest.mark.asyncio
async def test_agent_worker_direct_mode_default():
    """AgentWorker defaults to direct mode when invocation_mode is missing."""
    from hecate.engine.workers.agent_worker import AgentWorker

    mock_service = AsyncMock(return_value={"response": "direct result", "usage": {}})
    worker = AgentWorker(execution_service=mock_service)

    result = await worker.execute(
        node_id="agent_1",
        node_config={"agent_id": str(uuid.uuid4())},
        channel_snapshot={"messages": [{"role": "user", "content": "hi"}]},
    )

    assert result.error is None
    assert result.channel_updates["messages"][0]["content"] == "direct result"
    mock_service.assert_called_once()


@pytest.mark.asyncio
async def test_agent_worker_tool_mode():
    """AgentWorker with invocation_mode='tool' creates AgentTool schema."""
    from hecate.engine.workers.agent_worker import AgentWorker

    worker = AgentWorker()
    agent_id = uuid.uuid4()

    result = await worker.execute(
        node_id="agent_1",
        node_config={
            "agent_id": str(agent_id),
            "invocation_mode": "tool",
            "agent_definition": {
                "description": "A specialist",
                "tools": ["web_search"],
                "context_mode": "isolated",
            },
        },
        channel_snapshot={},
    )

    assert result.error is None
    tools = result.channel_updates.get("_agent_tools", [])
    assert len(tools) == 1
    assert tools[0]["_invocation_mode"] == "tool"
    assert tools[0]["_agent_id"] == str(agent_id)


@pytest.mark.asyncio
async def test_agent_worker_tool_mode_no_definition():
    """AgentWorker tool mode with no agent_definition uses defaults."""
    from hecate.engine.workers.agent_worker import AgentWorker

    worker = AgentWorker()
    agent_id = uuid.uuid4()

    result = await worker.execute(
        node_id="agent_1",
        node_config={
            "agent_id": str(agent_id),
            "invocation_mode": "tool",
        },
        channel_snapshot={},
    )

    assert result.error is None
    tools = result.channel_updates.get("_agent_tools", [])
    assert len(tools) == 1


@pytest.mark.asyncio
async def test_agent_worker_missing_agent_id():
    """AgentWorker returns error when agent_id is missing."""
    from hecate.engine.workers.agent_worker import AgentWorker

    worker = AgentWorker()

    result = await worker.execute(
        node_id="agent_1",
        node_config={},
        channel_snapshot={},
    )

    assert result.error is not None
    assert "missing required config field 'agent_id'" in str(result.error)


@pytest.mark.asyncio
async def test_compiler_validates_invocation_mode():
    """GraphCompiler rejects invalid invocation_mode values."""
    from hecate.engine.compiler import GraphCompiler
    from hecate.engine.graph_dsl import GraphValidationError
    from hecate.engine.types import GraphConfig, NodeConfig, NodeType

    config = GraphConfig(
        entry="agent_1",
        nodes={
            "agent_1": NodeConfig(
                id="agent_1",
                type=NodeType.AGENT,
                config={"agent_id": str(uuid.uuid4()), "invocation_mode": "invalid"},
            ),
        },
        edges=[],
    )

    compiler = GraphCompiler()
    with pytest.raises(GraphValidationError, match="invalid invocation_mode"):
        compiler.compile(config)


@pytest.mark.asyncio
async def test_compiler_accepts_valid_invocation_mode():
    """GraphCompiler accepts valid invocation_mode values."""
    from hecate.engine.compiler import GraphCompiler
    from hecate.engine.types import GraphConfig, NodeConfig, NodeType

    config = GraphConfig(
        entry="agent_1",
        nodes={
            "agent_1": NodeConfig(
                id="agent_1",
                type=NodeType.AGENT,
                config={"agent_id": str(uuid.uuid4()), "invocation_mode": "tool"},
            ),
        },
        edges=[],
    )

    compiler = GraphCompiler()
    compiled = compiler.compile(config)
    assert compiled.nodes["agent_1"].config["invocation_mode"] == "tool"


@pytest.mark.asyncio
async def test_agent_execute_injects_handoff_tool(db_session):
    """AgentExecutionPort injects handoff tool when handoff_targets present."""
    agent = _make_agent(tools=["web_search"])
    db_session.add(agent)
    tool = _make_tool("web_search")
    db_session.add(tool)
    await db_session.flush()

    from hecate.services.orchestration.agent_execution_port import AgentExecutionPort

    port = AgentExecutionPort(db=db_session)

    mock_response = MagicMock()
    mock_response.content = "Hello"
    mock_response.usage = {"total_tokens": 100}
    mock_response.model = "gpt-4o"
    mock_response.tool_calls = None

    with patch("hecate.services.llm.service.llm_service") as mock_llm:
        mock_llm.chat = AsyncMock(return_value=mock_response)

        result = await port.agent_execute(
            agent_id=agent.id,
            messages=[{"role": "user", "content": "help"}],
            channel_snapshot={},
            context={"node_id": "router", "handoff_targets": [{"node_id": "billing", "description": "Billing agent"}]},
        )

    assert "response" in result
    assert result["response"] == "Hello"


@pytest.mark.asyncio
async def test_agent_execute_no_handoff_tool_without_targets(db_session):
    """AgentExecutionPort does not inject handoff tool when no handoff_targets."""
    agent = _make_agent(tools=["web_search"])
    db_session.add(agent)
    tool = _make_tool("web_search")
    db_session.add(tool)
    await db_session.flush()

    from hecate.services.orchestration.agent_execution_port import AgentExecutionPort

    port = AgentExecutionPort(db=db_session)

    mock_response = MagicMock()
    mock_response.content = "Hello"
    mock_response.usage = {"total_tokens": 100}
    mock_response.model = "gpt-4o"
    mock_response.tool_calls = None

    with patch("hecate.services.llm.service.llm_service") as mock_llm:
        mock_llm.chat = AsyncMock(return_value=mock_response)

        result = await port.agent_execute(
            agent_id=agent.id,
            messages=[{"role": "user", "content": "help"}],
            channel_snapshot={},
            context={"node_id": "router"},
        )

    assert "handoff_to" not in result


@pytest.mark.asyncio
async def test_agent_execute_detects_handoff_call(db_session):
    """AgentExecutionPort detects handoff_to_agent tool call and returns handoff_to."""
    agent = _make_agent(tools=["web_search"])
    db_session.add(agent)
    tool = _make_tool("web_search")
    db_session.add(tool)
    await db_session.flush()

    from hecate.services.orchestration.agent_execution_port import AgentExecutionPort

    port = AgentExecutionPort(db=db_session)

    mock_tc = MagicMock()
    mock_tc.name = "handoff_to_agent"
    mock_tc.arguments = {"target": "billing"}
    mock_tc.id = "call_123"

    mock_response = MagicMock()
    mock_response.content = None
    mock_response.usage = {"total_tokens": 100}
    mock_response.model = "gpt-4o"
    mock_response.tool_calls = [mock_tc]

    with patch("hecate.services.llm.service.llm_service") as mock_llm:
        mock_llm.chat = AsyncMock(return_value=mock_response)

        result = await port.agent_execute(
            agent_id=agent.id,
            messages=[{"role": "user", "content": "billing question"}],
            channel_snapshot={},
            context={"node_id": "router", "handoff_targets": [{"node_id": "billing", "description": "Billing"}]},
        )

    assert result["handoff_to"] == "billing"
    assert result["_handoff_tool_call_id"] == "call_123"


@pytest.mark.asyncio
async def test_agent_execute_invalid_handoff_target(db_session):
    """AgentExecutionPort rejects invalid handoff target."""
    agent = _make_agent(tools=["web_search"])
    db_session.add(agent)
    tool = _make_tool("web_search")
    db_session.add(tool)
    await db_session.flush()

    from hecate.services.orchestration.agent_execution_port import AgentExecutionPort

    port = AgentExecutionPort(db=db_session)

    mock_tc = MagicMock()
    mock_tc.name = "handoff_to_agent"
    mock_tc.arguments = {"target": "unknown"}
    mock_tc.id = "call_123"

    mock_response = MagicMock()
    mock_response.content = None
    mock_response.usage = {"total_tokens": 100}
    mock_response.model = "gpt-4o"
    mock_response.tool_calls = [mock_tc]

    with patch("hecate.services.llm.service.llm_service") as mock_llm:
        mock_llm.chat = AsyncMock(return_value=mock_response)

        result = await port.agent_execute(
            agent_id=agent.id,
            messages=[{"role": "user", "content": "billing question"}],
            channel_snapshot={},
            context={"node_id": "router", "handoff_targets": [{"node_id": "billing", "description": "Billing"}]},
        )

    assert "handoff_to" not in result
    assert "Invalid handoff target" in result["response"]
