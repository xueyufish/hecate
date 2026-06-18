"""Tests for ToolGateEvaluator — platform-level tool gating."""

from __future__ import annotations

import logging

from hecate.engine.tool_gate import ToolGateEvaluator


class TestToolGateEvaluatorEvaluate:
    """Tests for evaluate() method."""

    def test_simple_equality_true(self) -> None:
        evaluator = ToolGateEvaluator()
        assert evaluator.evaluate("user_role == 'admin'", {"user_role": "admin"}) is True

    def test_simple_equality_false(self) -> None:
        evaluator = ToolGateEvaluator()
        assert evaluator.evaluate("user_role == 'admin'", {"user_role": "guest"}) is False

    def test_compound_and(self) -> None:
        evaluator = ToolGateEvaluator()
        context = {"phase": "EXECUTE", "budget": 5000}
        assert evaluator.evaluate("phase == 'EXECUTE' and budget > 1000", context) is True

    def test_compound_or(self) -> None:
        evaluator = ToolGateEvaluator()
        context = {"phase": "EXECUTE", "budget": 500}
        assert evaluator.evaluate("phase == 'EXECUTE' or budget > 1000", context) is True

    def test_membership_in(self) -> None:
        evaluator = ToolGateEvaluator()
        context = {"permissions": ["read", "write", "delete"]}
        assert evaluator.evaluate("'delete' in permissions", context) is True

    def test_membership_not_in(self) -> None:
        evaluator = ToolGateEvaluator()
        context = {"permissions": ["read", "write"]}
        assert evaluator.evaluate("'delete' in permissions", context) is False

    def test_blocks_import(self) -> None:
        evaluator = ToolGateEvaluator()
        assert evaluator.evaluate("__import__('os').system('echo hi')", {}) is False

    def test_blocks_builtins(self) -> None:
        evaluator = ToolGateEvaluator()
        assert evaluator.evaluate("print('hello')", {}) is False

    def test_undefined_variable_fails_closed(self) -> None:
        evaluator = ToolGateEvaluator()
        assert evaluator.evaluate("user_role == 'admin'", {}) is False

    def test_syntax_error_fails_closed(self) -> None:
        evaluator = ToolGateEvaluator()
        assert evaluator.evaluate("user_role == ", {"user_role": "admin"}) is False

    def test_type_error_fails_closed(self) -> None:
        evaluator = ToolGateEvaluator()
        assert evaluator.evaluate("user_role + 1", {"user_role": "admin"}) is False

    def test_logs_warning_on_failure(self, caplog) -> None:
        evaluator = ToolGateEvaluator()
        with caplog.at_level(logging.WARNING):
            evaluator.evaluate("nonexistent_var == 1", {})
        assert "expression evaluation failed" in caplog.text


class TestToolGateEvaluatorFilterTools:
    """Tests for filter_tools() method."""

    def test_mixed_tools(self) -> None:
        evaluator = ToolGateEvaluator()
        tools = [
            {"name": "always_tool", "description": "always available"},
            {"name": "admin_tool", "description": "admin only", "available_when": "user_role == 'admin'"},
            {"name": "guest_tool", "description": "guest only", "available_when": "user_role == 'guest'"},
        ]
        context = {"user_role": "admin"}
        result = evaluator.filter_tools(tools, context)
        names = [t["name"] for t in result]
        assert names == ["always_tool", "admin_tool"]

    def test_all_filtered_out(self) -> None:
        evaluator = ToolGateEvaluator()
        tools = [
            {"name": "a", "available_when": "user_role == 'admin'"},
            {"name": "b", "available_when": "user_role == 'superadmin'"},
        ]
        result = evaluator.filter_tools(tools, {"user_role": "guest"})
        assert result == []

    def test_empty_input(self) -> None:
        evaluator = ToolGateEvaluator()
        assert evaluator.filter_tools([], {"user_role": "admin"}) == []

    def test_none_available_when_passes_through(self) -> None:
        evaluator = ToolGateEvaluator()
        tools = [{"name": "tool", "available_when": None}]
        result = evaluator.filter_tools(tools, {})
        assert len(result) == 1

    def test_preserves_tool_dict_structure(self) -> None:
        evaluator = ToolGateEvaluator()
        original = {"name": "tool", "description": "desc", "parameters": {"type": "object"}, "available_when": "True"}
        tools = [original]
        result = evaluator.filter_tools(tools, {})
        assert result[0] is original

    def test_no_mutation_of_input(self) -> None:
        evaluator = ToolGateEvaluator()
        tools = [{"name": "a", "available_when": "user_role == 'admin'"}]
        evaluator.filter_tools(tools, {"user_role": "guest"})
        assert tools[0]["name"] == "a"
        assert "available_when" in tools[0]
