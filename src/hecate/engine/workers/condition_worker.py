"""Condition worker for evaluating expressions against channel state.

Evaluates the expression from node config against the current channel snapshot
and writes a ``_route`` value to channel_updates for the PregelRuntime's
conditional edge resolution.
"""

from __future__ import annotations

import logging
from typing import Any

from hecate.engine.types import WorkerResult
from hecate.engine.worker import Worker

logger = logging.getLogger(__name__)


class ConditionWorker(Worker):
    """Worker that evaluates conditional expressions and determines graph routing.

    Supports the following expression patterns:
    - ``has_tool_call``: checks if ``_has_tool_call`` is truthy in channel state.
    - ``<key> == '<value>'``: checks if a channel key equals a string value.
    - ``<key>``: checks if a channel key is truthy (used for generic routing).

    The result is written as ``_route`` in channel_updates: either ``"true"``
    or ``"false"``, matching the conditional edge target keys in the graph.
    """

    async def execute(
        self,
        node_id: str,
        node_config: dict,
        channel_snapshot: dict,
        execution_context: dict | None = None,
    ) -> WorkerResult:
        expression = node_config.get("expression", "")
        route_value = self._evaluate(expression, channel_snapshot)

        return WorkerResult(
            node_id=node_id,
            channel_updates={
                "_route": route_value,
                "messages": [],
            },
        )

    def _evaluate(self, expression: str, state: dict[str, Any]) -> str:
        """Evaluate an expression against channel state.

        Args:
            expression: The expression string from node config.
            state: The current channel state snapshot.

        Returns:
            ``"true"`` or ``"false"`` as a string for edge resolution.
        """
        if expression == "has_tool_call":
            return "true" if state.get("_has_tool_call") else "false"

        if " == " in expression:
            parts = expression.split(" == ", 1)
            key = parts[0].strip()
            value = parts[1].strip().strip("'\"")
            actual = str(state.get(key, ""))
            return "true" if actual == value else "false"

        if expression in state:
            return "true" if state[expression] else "false"

        logger.warning("Unrecognized condition expression: %s", expression)
        return "false"
