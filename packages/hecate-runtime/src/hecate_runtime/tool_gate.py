"""Tool gating evaluator for platform-level tool visibility.

Implements the ``ToolGateEvaluator`` that evaluates ``available_when``
conditional expressions against runtime context to determine which tools
are visible to the LLM on each invocation.

Design decisions (see openspec/changes/platform-tool-gating/design.md):
- Python-safe ``eval()`` with restricted namespace (no builtins, no imports)
- Fail-closed: any evaluation error → tool hidden (logged as WARNING)
- Flat context dict from execution_context + channel_snapshot
- Soft gate only — filtered tools are hidden from LLM, not blocked at execution
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class ToolGateEvaluator:
    """Evaluates ``available_when`` expressions against runtime context.

    Each tool may define an ``available_when`` string containing a Python
    expression.  The evaluator runs the expression in a restricted namespace
    (``__builtins__: {}``) with only the provided context variables in scope.

    On any evaluation error (NameError, SyntaxError, TypeError, etc.) the
    evaluator returns ``False`` (fail-closed) and logs a WARNING.
    """

    def evaluate(self, expression: str, context: dict[str, Any]) -> bool:
        """Evaluate a single ``available_when`` expression.

        Args:
            expression: Python expression string (e.g. ``"user_role == 'admin'"``).
            context: Flat dict of runtime variables available to the expression.

        Returns:
            ``True`` if the expression evaluates to a truthy value,
            ``False`` otherwise (including on any exception).
        """
        try:
            result = eval(expression, {"__builtins__": {}}, context)  # noqa: S307
            return bool(result)
        except Exception:
            logger.warning(
                "ToolGateEvaluator: expression evaluation failed for %r with context %s",
                expression,
                list(context.keys()),
                exc_info=True,
            )
            return False

    def filter_tools(
        self,
        tools: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Filter tool list, removing tools whose ``available_when`` is False.

        Tools without ``available_when`` or with ``available_when=None`` are
        always available and pass through unchanged.

        Args:
            tools: List of tool definition dicts.  Each dict may contain an
                ``available_when`` key with a string expression.
            context: Flat dict of runtime variables for expression evaluation.

        Returns:
            Filtered list containing only tools that pass the gate.
        """
        return [tool for tool in tools if self._is_available(tool, context)]

    def _is_available(self, tool: dict[str, Any], context: dict[str, Any]) -> bool:
        """Check if a single tool is available given the context.

        Args:
            tool: Tool definition dict.
            context: Runtime context variables.

        Returns:
            ``True`` if the tool is available, ``False`` otherwise.
        """
        expression = tool.get("available_when")
        if expression is None:
            return True
        return self.evaluate(expression, context)
