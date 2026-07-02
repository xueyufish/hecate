"""Unit tests for ToolFilter."""

from __future__ import annotations

from hecate.services.context.tool_filter import ToolFilter
from hecate.services.context.types import PhaseToolMapping, TaskPhase


class TestToolFilter:
    """Tests for the ToolFilter class."""

    def test_filter_tools_explore_phase(self) -> None:
        """Test tool filtering for explore phase."""
        tool_filter = ToolFilter()

        tools = [
            {"type": "function", "function": {"name": "web_search", "description": "Search web"}},
            {"type": "function", "function": {"name": "write_file", "description": "Write file"}},
            {"type": "function", "function": {"name": "read_file", "description": "Read file"}},
        ]

        filtered = tool_filter.filter_tools(tools, TaskPhase.EXPLORE)

        # Explore phase should include search tools
        names = [t["function"]["name"] for t in filtered]
        assert "web_search" in names

    def test_filter_tools_execute_phase(self) -> None:
        """Test tool filtering for execute phase (all tools)."""
        tool_filter = ToolFilter()

        tools = [
            {"type": "function", "function": {"name": "web_search", "description": "Search web"}},
            {"type": "function", "function": {"name": "write_file", "description": "Write file"}},
            {"type": "function", "function": {"name": "execute_code", "description": "Execute code"}},
        ]

        filtered = tool_filter.filter_tools(tools, TaskPhase.EXECUTE)

        # Execute phase returns all tools (empty mapping)
        assert len(filtered) == len(tools)

    def test_filter_tools_verify_phase(self) -> None:
        """Test tool filtering for verify phase."""
        tool_filter = ToolFilter()

        tools = [
            {"type": "function", "function": {"name": "run_tests", "description": "Run tests"}},
            {"type": "function", "function": {"name": "write_file", "description": "Write file"}},
            {"type": "function", "function": {"name": "check_lint", "description": "Check linting"}},
        ]

        filtered = tool_filter.filter_tools(tools, TaskPhase.VERIFY)

        # Verify phase should include test/check tools
        names = [t["function"]["name"] for t in filtered]
        assert "run_tests" in names or "check_lint" in names

    def test_filter_tools_empty_list(self) -> None:
        """Test filtering empty tool list."""
        tool_filter = ToolFilter()
        filtered = tool_filter.filter_tools([], TaskPhase.EXPLORE)
        assert filtered == []

    def test_filter_tools_no_matches_returns_all(self) -> None:
        """Test that all tools are returned if no matches found."""
        tool_filter = ToolFilter()

        # Tools with names that don't match any category
        tools = [
            {"type": "function", "function": {"name": "xyz_abc", "description": "Unknown tool"}},
        ]

        filtered = tool_filter.filter_tools(tools, TaskPhase.EXPLORE)

        # Should return all tools as fallback
        assert len(filtered) == 1

    def test_filter_tools_custom_mapping(self) -> None:
        """Test tool filtering with custom phase mapping."""
        custom_mapping = PhaseToolMapping(
            explore=["custom_search"],
            converge=["custom_search", "custom_edit"],
            execute=[],
            verify=["custom_check"],
        )
        tool_filter = ToolFilter(mapping=custom_mapping)

        tools = [
            {"type": "function", "function": {"name": "custom_search", "description": "Custom search"}},
            {"type": "function", "function": {"name": "custom_edit", "description": "Custom edit"}},
            {"type": "function", "function": {"name": "custom_check", "description": "Custom check"}},
        ]

        # Explore phase should only include custom_search
        filtered = tool_filter.filter_tools(tools, TaskPhase.EXPLORE)
        names = [t["function"]["name"] for t in filtered]
        assert "custom_search" in names

    def test_get_tool_name_standard_format(self) -> None:
        """Test extracting tool name from standard format."""
        tool_filter = ToolFilter()
        tool = {"type": "function", "function": {"name": "my_tool"}}

        name = tool_filter._get_tool_name(tool)
        assert name == "my_tool"

    def test_get_tool_name_empty_function(self) -> None:
        """Test extracting tool name when function is empty."""
        tool_filter = ToolFilter()
        tool = {"type": "function", "function": {}}

        name = tool_filter._get_tool_name(tool)
        assert name == ""

    def test_get_tool_name_no_function(self) -> None:
        """Test extracting tool name when no function key."""
        tool_filter = ToolFilter()
        tool = {"type": "other"}

        name = tool_filter._get_tool_name(tool)
        assert name == ""

    def test_matches_categories_read(self) -> None:
        """Test category matching for read tools."""
        tool_filter = ToolFilter()

        assert tool_filter._matches_categories("read_file", ["read"]) is True
        assert tool_filter._matches_categories("get_data", ["read"]) is True
        assert tool_filter._matches_categories("fetch_results", ["read"]) is True
        assert tool_filter._matches_categories("load_config", ["read"]) is True
        assert tool_filter._matches_categories("view_details", ["read"]) is True

    def test_matches_categories_write(self) -> None:
        """Test category matching for write tools."""
        tool_filter = ToolFilter()

        assert tool_filter._matches_categories("write_file", ["write"]) is True
        assert tool_filter._matches_categories("create_project", ["write"]) is True
        assert tool_filter._matches_categories("save_data", ["write"]) is True
        assert tool_filter._matches_categories("update_config", ["write"]) is True

    def test_matches_categories_search(self) -> None:
        """Test category matching for search tools."""
        tool_filter = ToolFilter()

        assert tool_filter._matches_categories("search_web", ["search"]) is True
        assert tool_filter._matches_categories("find_files", ["search"]) is True
        assert tool_filter._matches_categories("grep_code", ["search"]) is True
        assert tool_filter._matches_categories("glob_pattern", ["search"]) is True
        assert tool_filter._matches_categories("list_items", ["search"]) is True

    def test_matches_categories_test(self) -> None:
        """Test category matching for test tools."""
        tool_filter = ToolFilter()

        assert tool_filter._matches_categories("run_tests", ["test"]) is True
        assert tool_filter._matches_categories("check_lint", ["test"]) is True
        assert tool_filter._matches_categories("verify_output", ["test"]) is True
        assert tool_filter._matches_categories("validate_input", ["test"]) is True

    def test_matches_categories_execute(self) -> None:
        """Test category matching for execute tools."""
        tool_filter = ToolFilter()

        assert tool_filter._matches_categories("run_command", ["execute"]) is True
        assert tool_filter._matches_categories("exec_script", ["execute"]) is True
        assert tool_filter._matches_categories("bash_shell", ["execute"]) is True

    def test_matches_categories_no_match(self) -> None:
        """Test category matching with no match."""
        tool_filter = ToolFilter()

        assert tool_filter._matches_categories("unknown_tool", ["read"]) is False
        assert tool_filter._matches_categories("random_name", ["search", "write"]) is False

    def test_get_allowed_categories_explore(self) -> None:
        """Test getting allowed categories for explore phase."""
        tool_filter = ToolFilter()
        categories = tool_filter._get_allowed_categories(TaskPhase.EXPLORE)
        assert "search" in categories
        assert "read" in categories

    def test_get_allowed_categories_execute(self) -> None:
        """Test getting allowed categories for execute phase."""
        tool_filter = ToolFilter()
        categories = tool_filter._get_allowed_categories(TaskPhase.EXECUTE)
        # Execute phase has empty mapping (all tools allowed)
        assert categories == []
