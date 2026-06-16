"""Sub-graph execution with parent-child channel mapping.

Provides the ``execute_subgraph`` helper that bridges a parent graph's channels
to a child graph's channels, runs the child graph to completion in its own
PregelRuntime, and propagates the final state back to the parent. This enables
hierarchical graph composition where an AGENT-type node delegates to a nested
graph definition.
"""

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

    **Channel mapping:** ``channel_mapping`` is a dict of ``{parent_channel_name:
    child_channel_name}`` pairs that controls how data flows between the two
    graphs. Before execution, each mapped parent channel is read and its value
    is written to the corresponding child channel as initial input. After the
    sub-graph completes, each mapped child channel's final value is written back
    to the parent channel.

    **Sub-runtime lifecycle:** A new PregelRuntime is created for the sub-graph
    with its own ChannelManager, so the sub-graph executes in complete isolation
    from the parent. The sub-graph runs to completion (no interrupt/resume is
    propagated to the parent).

    **State propagation:** Only the final state of the sub-graph (the last
    VALUES event) is propagated back to the parent channels.

    Args:
        parent_graph: The parent graph definition (unused directly, kept for
            future extensibility).
        sub_graph: The compiled sub-graph to execute.
        parent_channels: The parent graph's ChannelManager to read inputs from
            and write outputs to.
        worker: The Worker instance to use for node execution within the sub-graph.
        checkpoint_store: Shared checkpoint store (sub-graph checkpoints are
            stored under the same session_id).
        session_id: The parent session ID, reused for the sub-graph.
        channel_mapping: Optional dict mapping parent channel names to child
            channel names. Defaults to ``{"messages": "messages", "context":
            "context"}``.

    Returns:
        The final channel state dict of the sub-graph.
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
