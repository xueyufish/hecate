from __future__ import annotations

import uuid
from typing import Any

from hecate.engine.channel import ChannelManager
from hecate.engine.checkpoint import CheckpointStore
from hecate.engine.pregel import PregelRuntime
from hecate.engine.types import CompiledGraph, StreamMode
from hecate.engine.worker import Worker


async def execute_subgraph(
    parent_graph: CompiledGraph,
    sub_graph: CompiledGraph,
    parent_channels: ChannelManager,
    worker: Worker,
    checkpoint_store: CheckpointStore,
    session_id: uuid.UUID,
    channel_mapping: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Execute a sub-graph and map its final state back to the parent channels.

    Reads input from parent channels via channel_mapping, runs the sub-graph
    to completion, then writes the final state back to parent channels.
    """
    mapping = channel_mapping or {"messages": "messages", "context": "context"}

    sub_input: dict[str, Any] = {}
    for parent_ch, child_ch in mapping.items():
        sub_input[child_ch] = parent_channels.read(parent_ch)

    sub_runtime = PregelRuntime(
        graph=sub_graph,
        worker=worker,
        checkpoint_store=checkpoint_store,
    )

    results = []
    async for event in sub_runtime.execute(
        session_id=session_id,
        initial_input=sub_input,
        stream_mode=StreamMode.VALUES,
    ):
        results.append(event)

    final_state = results[-1].get("state", {}) if results else {}

    for parent_ch, child_ch in mapping.items():
        if child_ch in final_state:
            parent_channels.write(parent_ch, final_state[child_ch])

    return final_state
