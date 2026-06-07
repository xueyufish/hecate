"""Tests for ToolRegistry routing and seed function."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from hecate.models.tool import ToolModel
from hecate.services.tool.builtin import BuiltInToolExecutor
from hecate.services.tool.registry import ToolRegistry, seed_builtin_tools
from hecate.services.tool.search import SearchProvider


class MockSearchProvider(SearchProvider):
    async def search(self, query: str, max_results: int = 5) -> list[dict]:
        return [{"title": "test", "url": "https://test.com", "snippet": "test"}]


@pytest.fixture
def mock_builtin_executor(tmp_path: Any) -> BuiltInToolExecutor:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return BuiltInToolExecutor(
        search_provider=MockSearchProvider(),
        workspace_root=str(workspace),
    )


class TestRegistryBuiltinRouting:
    async def test_builtin_tool_resolves(self, db_session: Any, mock_builtin_executor: BuiltInToolExecutor) -> None:
        registry = ToolRegistry(db=db_session, builtin_executor=mock_builtin_executor)
        result = await registry.execute("web_search", {"query": "test"})
        assert isinstance(result, list)

    async def test_unknown_tool_raises(self, db_session: Any, mock_builtin_executor: BuiltInToolExecutor) -> None:
        registry = ToolRegistry(db=db_session, builtin_executor=mock_builtin_executor)
        with pytest.raises(ValueError, match="not found"):
            await registry.execute("nonexistent_tool", {})

    async def test_custom_tool_not_implemented(
        self, db_session: Any, mock_builtin_executor: BuiltInToolExecutor
    ) -> None:
        tool = ToolModel(
            workspace_id=uuid.UUID("00000000-0000-0000-0000-000000000000"),
            name="my_custom_tool",
            description="A custom tool",
            source="custom",
            parameters={"type": "object", "properties": {}},
        )
        db_session.add(tool)
        await db_session.flush()

        registry = ToolRegistry(db=db_session, builtin_executor=mock_builtin_executor)
        with pytest.raises(NotImplementedError, match="Custom tool"):
            await registry.execute("my_custom_tool", {})

    async def test_mcp_tool_no_manager(self, db_session: Any, mock_builtin_executor: BuiltInToolExecutor) -> None:
        tool = ToolModel(
            workspace_id=uuid.UUID("00000000-0000-0000-0000-000000000000"),
            name="mcp_tool",
            description="An MCP tool",
            source="mcp",
            parameters={"type": "object", "properties": {}},
            mcp_server="http://localhost:8080",
            mcp_tool_name="mcp_tool",
        )
        db_session.add(tool)
        await db_session.flush()

        registry = ToolRegistry(db=db_session, builtin_executor=mock_builtin_executor)
        with pytest.raises(RuntimeError, match="MCPClientManager not configured"):
            await registry.execute("mcp_tool", {})


class TestSeedBuiltinTools:
    async def test_seed_inserts_all_tools(self, db_session: Any) -> None:
        count = await seed_builtin_tools(db_session)
        assert count == 5

        from sqlalchemy import select

        result = await db_session.execute(select(ToolModel).where(ToolModel.source == "builtin"))
        tools = result.scalars().all()
        assert len(tools) == 5
        names = {t.name for t in tools}
        assert names == {"web_search", "read_file", "write_file", "list_files", "execute_code"}

    async def test_seed_idempotent(self, db_session: Any) -> None:
        count1 = await seed_builtin_tools(db_session)
        assert count1 == 5
        count2 = await seed_builtin_tools(db_session)
        assert count2 == 0

    async def test_seed_updates_changed_definitions(self, db_session: Any) -> None:
        await seed_builtin_tools(db_session)

        from sqlalchemy import select

        result = await db_session.execute(select(ToolModel).where(ToolModel.name == "web_search"))
        tool = result.scalar_one()
        tool.description = "old description"
        await db_session.flush()

        count = await seed_builtin_tools(db_session)
        assert count == 1

        result = await db_session.execute(select(ToolModel).where(ToolModel.name == "web_search"))
        tool = result.scalar_one()
        assert tool.description != "old description"
