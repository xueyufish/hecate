"""Tests for AgentDefinition and AgentTool."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

from hecate.engine.agent_tool import AgentDefinition, AgentTool


class TestAgentDefinition:
    """Tests for AgentDefinition dataclass."""

    def test_minimal_creation(self) -> None:
        uid = uuid4()
        defn = AgentDefinition(agent_id=uid, description="Research assistant")
        assert defn.agent_id == uid
        assert defn.description == "Research assistant"
        assert defn.tools is None
        assert defn.disallowed_tools == ["agent_execute"]
        assert defn.context_mode == "inherited"
        assert defn.prompt_override is None
        assert defn.model_override is None
        assert defn.max_turns is None
        assert defn.timeout_seconds is None
        assert defn.skills is None

    def test_full_creation(self) -> None:
        uid = uuid4()
        defn = AgentDefinition(
            agent_id=uid,
            description="Researcher",
            prompt_override="Custom prompt",
            tools=["web_search", "read"],
            disallowed_tools=["agent_execute"],
            skills=["research"],
            model_override="gpt-4o-mini",
            context_mode="isolated",
            max_turns=5,
            timeout_seconds=60.0,
        )
        assert defn.tools == ["web_search", "read"]
        assert defn.context_mode == "isolated"
        assert defn.max_turns == 5
        assert defn.timeout_seconds == 60.0
        assert defn.model_override == "gpt-4o-mini"


class TestAgentToolName:
    """Tests for AgentTool name/description derivation."""

    def test_name_with_agent_name(self) -> None:
        defn = AgentDefinition(agent_id=uuid4(), description="Searches the web")
        tool = AgentTool(defn, agent_name="researcher")
        assert tool.name == "agent_researcher"

    def test_name_without_agent_name(self) -> None:
        uid = uuid4()
        defn = AgentDefinition(agent_id=uid, description="Searches the web")
        tool = AgentTool(defn)
        assert tool.name.startswith("agent_")
        assert tool.name != "agent_"

    def test_description(self) -> None:
        defn = AgentDefinition(agent_id=uuid4(), description="Does research")
        tool = AgentTool(defn, agent_name="research")
        assert tool.description == "Does research"


class TestToolResolution:
    """Tests for whitelist/blacklist resolution."""

    def test_whitelist_minus_blacklist(self) -> None:
        defn = AgentDefinition(
            agent_id=uuid4(),
            description="test",
            tools=["web_search", "read", "agent_execute"],
            disallowed_tools=["agent_execute"],
        )
        tool = AgentTool(defn, agent_name="test")
        result = tool.resolve_tools()
        assert result == ["web_search", "read"]

    def test_inherit_minus_blacklist(self) -> None:
        defn = AgentDefinition(
            agent_id=uuid4(),
            description="test",
            tools=None,
            disallowed_tools=["agent_execute", "write"],
        )
        tool = AgentTool(defn, agent_name="test")
        result = tool.resolve_tools(parent_tools=["web_search", "read", "write", "agent_execute"])
        assert result == ["web_search", "read"]

    def test_empty_whitelist(self) -> None:
        defn = AgentDefinition(
            agent_id=uuid4(),
            description="test",
            tools=[],
        )
        tool = AgentTool(defn, agent_name="test")
        result = tool.resolve_tools()
        assert result == []

    def test_inherit_no_parent(self) -> None:
        defn = AgentDefinition(
            agent_id=uuid4(),
            description="test",
            tools=None,
            disallowed_tools=["agent_execute"],
        )
        tool = AgentTool(defn, agent_name="test")
        result = tool.resolve_tools(parent_tools=None)
        assert result == []


class TestAgentToolExecute:
    """Tests for AgentTool.execute() behavior."""

    async def test_calls_agent_execute(self) -> None:
        uid = uuid4()
        defn = AgentDefinition(agent_id=uid, description="test", context_mode="isolated")
        tool = AgentTool(defn, agent_name="test")

        calls: list[dict] = []

        async def mock_agent_execute(**kwargs: object) -> dict:
            calls.append(kwargs)
            return {"response": "done", "usage": {}}

        port = SimpleNamespace(agent_execute=mock_agent_execute)
        result = await tool.execute({"task": "analyze data"}, port, {})
        assert result["response"] == "done"
        assert len(calls) == 1
        assert calls[0]["agent_id"] == uid

    async def test_isolated_context(self) -> None:
        uid = uuid4()
        defn = AgentDefinition(agent_id=uid, description="test", context_mode="isolated")
        tool = AgentTool(defn, agent_name="test")

        captured_messages: list = []

        async def mock_agent_execute(**kwargs: object) -> dict:
            captured_messages.extend(kwargs.get("messages", []))  # type: ignore[arg-type]
            return {"response": "done"}

        port = SimpleNamespace(agent_execute=mock_agent_execute)
        await tool.execute(
            {"task": "do work"},
            port,
            {"messages": [{"role": "user", "content": "parent history"}]},
        )
        assert len(captured_messages) == 1
        assert captured_messages[0]["content"] == "do work"

    async def test_inherited_context(self) -> None:
        uid = uuid4()
        defn = AgentDefinition(agent_id=uid, description="test", context_mode="inherited")
        tool = AgentTool(defn, agent_name="test")

        captured_messages: list = []

        async def mock_agent_execute(**kwargs: object) -> dict:
            captured_messages.extend(kwargs.get("messages", []))  # type: ignore[arg-type]
            return {"response": "done"}

        port = SimpleNamespace(agent_execute=mock_agent_execute)
        await tool.execute(
            {"task": "do work"},
            port,
            {"messages": [{"role": "user", "content": "parent history"}]},
        )
        assert len(captured_messages) == 2
        assert captured_messages[0]["content"] == "parent history"
        assert captured_messages[1]["content"] == "do work"

    async def test_timeout_enforcement(self) -> None:
        uid = uuid4()
        defn = AgentDefinition(
            agent_id=uid,
            description="test",
            timeout_seconds=0.01,
        )
        tool = AgentTool(defn, agent_name="test")

        async def slow_execute(**kwargs: object) -> dict:
            await asyncio.sleep(10)
            return {"response": "never"}

        port = SimpleNamespace(agent_execute=slow_execute)
        result = await tool.execute({"task": "slow"}, port, {})
        assert result.get("timed_out") is True
        assert "error" in result

    async def test_max_turns_in_context(self) -> None:
        uid = uuid4()
        defn = AgentDefinition(agent_id=uid, description="test", max_turns=3)
        tool = AgentTool(defn, agent_name="test")

        captured_ctx: dict = {}

        async def mock_agent_execute(**kwargs: object) -> dict:
            captured_ctx.update(kwargs.get("context", {}))  # type: ignore[arg-type]
            return {"response": "done"}

        port = SimpleNamespace(agent_execute=mock_agent_execute)
        await tool.execute({"task": "work"}, port, {})
        assert captured_ctx["max_turns"] == 3

    async def test_input_fallback(self) -> None:
        uid = uuid4()
        defn = AgentDefinition(agent_id=uid, description="test", context_mode="isolated")
        tool = AgentTool(defn, agent_name="test")

        captured_messages: list = []

        async def mock_agent_execute(**kwargs: object) -> dict:
            captured_messages.extend(kwargs.get("messages", []))  # type: ignore[arg-type]
            return {"response": "done"}

        port = SimpleNamespace(agent_execute=mock_agent_execute)
        await tool.execute({"input": "via input key"}, port, {})
        assert captured_messages[0]["content"] == "via input key"
