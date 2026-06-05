from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from hecate.services.workflow.execution_service import WorkflowExecutionService


def _make_port(tokens: list[str] | None = None) -> MagicMock:
    port = MagicMock()
    tokens = tokens or ["Hello!"]

    async def fake_context_assemble(*args, **kwargs):
        return {"messages": kwargs.get("messages", []), "tools": kwargs.get("tools"), "metadata": {}}

    port.context_assemble = AsyncMock(side_effect=fake_context_assemble)

    async def fake_llm_invoke(*args, **kwargs):
        for t in tokens:
            yield t

    port.llm_invoke = fake_llm_invoke
    port.tool_execute = AsyncMock(return_value="tool result")
    port.knowledge_query = AsyncMock(return_value="knowledge context")
    return port


class TestWorkflowExecutionServiceChatMode:
    async def test_basic_chat_non_streaming(self) -> None:
        port = _make_port(["Hello! How can I help?"])
        service = WorkflowExecutionService(port=port)
        result = await service.execute(
            agent_mode="chat",
            messages=[{"role": "user", "content": "Hi"}],
            model="gpt-4o",
        )
        assert "content" in result
        assert result["content"] == "Hello! How can I help?"
        assert result["model"] == "gpt-4o"
        assert result["finish_reason"] == "stop"

    async def test_chat_with_suggestions(self) -> None:
        port = _make_port(["Hi there!"])
        service = WorkflowExecutionService(port=port)
        result = await service.execute(
            agent_mode="chat",
            messages=[{"role": "user", "content": "Hello"}],
            enable_suggestions=True,
        )
        assert "content" in result

    async def test_chat_with_opening(self) -> None:
        port = _make_port(["Welcome!"])
        mock_suggestion = MagicMock()
        mock_suggestion.generate_opening = AsyncMock(
            return_value=MagicMock(questions=["What can you do?", "Help me code"])
        )
        service = WorkflowExecutionService(port=port, suggestion_service=mock_suggestion)
        result = await service.execute(
            agent_mode="chat",
            messages=[{"role": "user", "content": "Hello"}],
            generate_opening=True,
            agent_persona="Helpful assistant",
        )
        assert "content" in result

    async def test_chat_with_kb_ids(self) -> None:
        port = _make_port(["Based on the docs..."])
        service = WorkflowExecutionService(port=port)
        result = await service.execute(
            agent_mode="chat",
            messages=[{"role": "user", "content": "What is Hecate?"}],
            kb_ids=["kb-001", "kb-002"],
        )
        assert "content" in result

    async def test_chat_with_tools(self) -> None:
        port = _make_port(["I'll search for that."])
        service = WorkflowExecutionService(port=port)
        result = await service.execute(
            agent_mode="chat",
            messages=[{"role": "user", "content": "Search for X"}],
            tools=[{"type": "function", "function": {"name": "search", "parameters": {}}}],
        )
        assert "content" in result

    async def test_chat_unknown_mode_raises(self) -> None:
        port = _make_port()
        service = WorkflowExecutionService(port=port)
        try:
            await service.execute(agent_mode="invalid")
            raise AssertionError("Should have raised ValueError")
        except ValueError as e:
            assert "Unknown agent mode" in str(e)

    async def test_chat_custom_session_id(self) -> None:
        port = _make_port(["OK"])
        service = WorkflowExecutionService(port=port)
        result = await service.execute(
            agent_mode="chat",
            messages=[{"role": "user", "content": "Hi"}],
            session_id="12345678-1234-5678-1234-567812345678",
        )
        assert "content" in result

    async def test_chat_custom_system_prompt(self) -> None:
        port = _make_port(["OK"])
        service = WorkflowExecutionService(port=port)
        result = await service.execute(
            agent_mode="chat",
            messages=[{"role": "user", "content": "Hi"}],
            system_prompt="You are a coding assistant.",
        )
        assert "content" in result


class TestWorkflowExecutionServiceThreeLayerMode:
    async def test_three_layer_no_guard_node(self) -> None:
        from hecate.engine.templates import build_three_layer_graph

        graph = build_three_layer_graph(planner_model="gpt-4o", sub_agent_model="gpt-4o")
        assert "guard" not in graph.nodes
        assert graph.entry == "planner"
