"""Variable set worker for writing values to channels.

Reads ``variable_name`` and ``value`` from node config and writes them
directly to channel_updates, allowing workflow designers to explicitly
set state variables in the graph.
"""

from __future__ import annotations

from typing import Any

from hecate.engine.types import WorkerResult
from hecate.engine.worker import Worker


class VariableSetWorker(Worker):
    """Worker that writes configured variable values to channels.

    Expects node config to contain ``variable_name`` and ``value`` keys.
    Both values are written directly to channel_updates under the variable name.
    """

    async def execute(
        self,
        node_id: str,
        node_config: dict,
        channel_snapshot: dict,
    ) -> WorkerResult:
        var_name = node_config.get("variable_name", "")
        var_value = node_config.get("value", "")

        updates: dict[str, Any] = {"messages": []}
        if var_name:
            updates[var_name] = var_value

        return WorkerResult(node_id=node_id, channel_updates=updates)
