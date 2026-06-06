"""Tests for BuiltInToolExecutor — each tool with mock filesystem and sandbox."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from hecate.services.tool.builtin import BUILTIN_TOOL_DEFINITIONS, BuiltInToolExecutor
from hecate.services.tool.search import SearchProvider


class MockSearchProvider(SearchProvider):
    """Mock search provider for testing."""

    async def search(self, query: str, max_results: int = 5) -> list[dict]:
        return [
            {"title": f"Result for {query}", "url": "https://example.com", "snippet": "test snippet"},
        ]


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path / "workspace"


@pytest.fixture
def executor(workspace: Path) -> BuiltInToolExecutor:
    return BuiltInToolExecutor(
        search_provider=MockSearchProvider(),
        workspace_root=str(workspace),
    )


class TestWebSearch:
    async def test_basic_search(self, executor: BuiltInToolExecutor) -> None:
        result = await executor.execute("web_search", {"query": "test query"})
        assert len(result) == 1
        assert result[0]["title"] == "Result for test query"
        assert result[0]["url"] == "https://example.com"

    async def test_search_with_max_results(self, executor: BuiltInToolExecutor) -> None:
        result = await executor.execute("web_search", {"query": "test", "max_results": 3})
        assert isinstance(result, list)


class TestReadFile:
    async def test_read_existing_file(self, executor: BuiltInToolExecutor, workspace: Path) -> None:
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "test.txt").write_text("hello world")
        result = await executor.execute("read_file", {"path": "test.txt"})
        assert result == "hello world"

    async def test_read_nonexistent_raises(self, executor: BuiltInToolExecutor) -> None:
        with pytest.raises(FileNotFoundError):
            await executor.execute("read_file", {"path": "nonexistent.txt"})

    async def test_path_traversal_blocked(self, executor: BuiltInToolExecutor) -> None:
        with pytest.raises(ValueError, match="Path traversal"):
            await executor.execute("read_file", {"path": "../../etc/passwd"})


class TestWriteFile:
    async def test_write_new_file(self, executor: BuiltInToolExecutor, workspace: Path) -> None:
        result = await executor.execute("write_file", {"path": "output.txt", "content": "data"})
        assert "Written" in result
        assert (workspace / "output.txt").read_text() == "data"

    async def test_write_creates_parent_dirs(self, executor: BuiltInToolExecutor, workspace: Path) -> None:
        await executor.execute("write_file", {"path": "a/b/c.txt", "content": "nested"})
        assert (workspace / "a" / "b" / "c.txt").read_text() == "nested"

    async def test_write_absolute_path_blocked(self, executor: BuiltInToolExecutor) -> None:
        with pytest.raises(ValueError, match="Path traversal"):
            await executor.execute("write_file", {"path": "/etc/passwd", "content": "bad"})


class TestListFiles:
    async def test_list_root(self, executor: BuiltInToolExecutor, workspace: Path) -> None:
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "a.txt").touch()
        (workspace / "b.txt").touch()
        result = await executor.execute("list_files", {})
        assert sorted(result) == ["a.txt", "b.txt"]

    async def test_list_subdirectory(self, executor: BuiltInToolExecutor, workspace: Path) -> None:
        (workspace / "data").mkdir(parents=True)
        (workspace / "data" / "file.txt").touch()
        result = await executor.execute("list_files", {"path": "data"})
        assert result == ["file.txt"]

    async def test_list_nonexistent_raises(self, executor: BuiltInToolExecutor) -> None:
        with pytest.raises(FileNotFoundError):
            await executor.execute("list_files", {"path": "nonexistent"})


class TestExecuteCode:
    async def test_sandbox_unavailable(self, executor: BuiltInToolExecutor) -> None:
        with patch.dict("sys.modules", {"hecate.services.sandbox.executor": None}):
            result = await executor.execute("execute_code", {"code": "print(1)"})
            assert result["exit_code"] == -1
            assert "unavailable" in result["stderr"].lower()


class TestUnknownTool:
    async def test_unknown_tool_raises(self, executor: BuiltInToolExecutor) -> None:
        with pytest.raises(ValueError, match="Unknown built-in tool"):
            await executor.execute("nonexistent", {})


class TestToolDefinitions:
    def test_all_5_tools_defined(self) -> None:
        expected = {"web_search", "read_file", "write_file", "list_files", "execute_code"}
        assert set(BUILTIN_TOOL_DEFINITIONS.keys()) == expected

    def test_each_definition_has_required_keys(self) -> None:
        for name, defn in BUILTIN_TOOL_DEFINITIONS.items():
            assert "description" in defn, f"{name} missing description"
            assert "parameters" in defn, f"{name} missing parameters"
            assert "type" in defn["parameters"], f"{name} parameters missing type"
