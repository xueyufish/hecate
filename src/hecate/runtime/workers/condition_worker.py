"""Condition worker for evaluating expressions against channel state.

Evaluates the expression from node config against the current channel snapshot
and writes a ``_route`` value to channel_updates for the PregelRuntime's
conditional edge resolution.
"""

from __future__ import annotations

import logging
from typing import Any

from hecate.runtime.types import WorkerResult
from hecate.runtime.worker import Worker

logger = logging.getLogger(__name__)


class ConditionWorker(Worker):
    """Worker that evaluates conditional expressions and determines graph routing.

    Supports the following expression patterns:
    - ``has_tool_call``: checks if ``_has_tool_call`` is truthy in channel state.
    - ``<key> == '<value>'``: checks if a channel key equals a string value.
    - ``<key>``: checks if a channel key is truthy (used for generic routing).

    The result is written as ``_route`` in channel_updates: either ``"true"``
    or ``"false"``, matching the conditional edge target keys in the graph.

    Dynamic fan-out (1.3.21③): a CONDITION node may declare a ``fanout``
    block (``{"over": <channel>, "target": <node>, "state_key": <key>,
    "max_fanout": <int>}``). When set, the worker emits one ``DispatchPacket``
    per element of the over channel into ``_dispatch`` and clears ``_route``
    (the fan-out plan replaces the conditional edge walk for this superstep).
    """

    async def execute(
        self,
        node_id: str,
        node_config: dict,
        channel_snapshot: dict,
        execution_context: dict | None = None,
    ) -> WorkerResult:
        updates: dict[str, Any] = {"messages": []}
        fanout_cfg = node_config.get("fanout")
        if fanout_cfg:
            updates["_dispatch"] = self._build_dispatch(fanout_cfg, channel_snapshot)
            return WorkerResult(node_id=node_id, channel_updates=updates)

        expression = node_config.get("expression", "")
        updates["_route"] = self._evaluate(expression, channel_snapshot)
        return WorkerResult(node_id=node_id, channel_updates=updates)

    def _build_dispatch(self, fanout_cfg: dict[str, Any], channel_snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        """Read the over channel and emit one dispatch packet per element.

        The over channel's value is expected to be a list (or any iterable).
        Scalar values are treated as a one-element sequence; missing / None is
        an empty sequence (zero dispatches — see spec scenario).
        """
        over = fanout_cfg.get("over", "")
        target = fanout_cfg.get("target", "")
        state_key = fanout_cfg.get("state_key", "")
        value = channel_snapshot.get(over)
        if value is None:
            items: list[Any] = []
        elif isinstance(value, list):
            items = value
        else:
            items = [value]
        return [{"node": target, "state": {state_key: item}} for item in items]

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
