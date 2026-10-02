"""Dynamic-orchestration graph materialisation (1.3.18, step5b).

``build_dynamic_orchestration_executor`` is the single place where LLM
intent (a planner-produced ``TaskDAG``) becomes a ``GraphConfig`` that
``PregelRuntime`` can execute. Moved from
``hecate.studio.workflows.templates`` (step5b): it consumes only kernel
types, so the kernel owns it and studio keeps a forwarding shim.
"""

from __future__ import annotations

from typing import Any

from hecate_runtime.replay.orchestrator_validator import budget_topological_levels
from hecate_runtime.types import ChannelDef, ChannelType, Edge, GraphConfig, NodeConfig, NodeType


def _resolve_orchestrator_roster(roster: list[Any]) -> dict[str, Any]:
    """Adapt orchestrator-roster entries into a name → metadata map.

    Accepts either plain dicts (with ``agent_id`` / ``model`` /
    ``capabilities``) or objects exposing those attributes. Returns a
    dict keyed by agent_id so the executor can resolve per-task models
    and emit one ``AGENT`` node per task.
    """
    out: dict[str, Any] = {}
    for r in roster or []:
        agent_id = getattr(r, "agent_id", None) or (r.get("agent_id") if hasattr(r, "get") else None)
        if agent_id is None:
            continue
        out[agent_id] = r
    return out


def build_dynamic_orchestration_executor(
    dag: Any,
    roster: list[Any],
    *,
    revision: int = 0,
    ledger: dict[str, dict[str, Any]] | None = None,
) -> GraphConfig:
    """Materialise a planner-produced TaskDAG into a runnable GraphConfig.

    This is the one place where LLM intent (the TaskDAG) becomes a graph
    that the existing PregelRuntime can execute. The contract:

    * Each task becomes one ``AGENT`` node with id
      ``task_<task_id>_<revision>``.
    * Each task's output lands in a ``LAST_VALUE`` channel named
      ``<task_id>.<expected_output>``.
    * ``max_concurrent`` batches tasks within the same dependency level:
      levels with more tasks than ``max_concurrent`` are split into
      sequential batches of size ``max_concurrent`` chained by
      ``FAN_OUT``/``MERGE`` pairs.
    * ``synthesis_transform`` (if set) becomes a ``VARIABLE_SET`` node
      that runs deterministically before the synthesis ``AGENT`` node.
    * The synthesis ``AGENT`` node reads all per-task output channels
      and writes ``_synthesis_buffer``.
    * Tasks that declare a ``verify`` hook get a sibling
      ``verifier_<task_id>`` ``AGENT`` node that reads the task output
      and writes the ``verified`` flag.

    Args:
        dag: The validated ``TaskDAG`` instance.
        roster: The roster of ``RosterEntry`` (or dict-like) agents.
        revision: The plan revision this materialisation corresponds to
            (defaults to 0 for tests; the CoordinatorWorker supplies the
            current iteration counter at runtime).
        ledger: Optional carryover ledger used to pre-populate the
            sub-graph's initial input channels (replan-with-carryover).
    """
    roster_map = _resolve_orchestrator_roster(roster)
    budgets = dag.budgets
    max_concurrent = budgets.max_concurrent

    nodes: dict[str, NodeConfig] = {}
    state: dict[str, ChannelDef] = {
        "messages": ChannelDef(type=ChannelType.TOPIC, default=[]),
        "_ledger": ChannelDef(type=ChannelType.LAST_VALUE, default={}),
        "_synthesis_buffer": ChannelDef(type=ChannelType.LAST_VALUE, default=None),
    }

    # Pre-seed task-output channels for carryover so VERIFIED/synthesis
    # can find values without a fresh execution.
    if ledger:
        for task_id, outputs in ledger.items():
            for output_name, value in outputs.items():
                state[f"{task_id}.{output_name}"] = ChannelDef(
                    type=ChannelType.LAST_VALUE,
                    default=value,
                )

    # Track dependency level → list of (task_id, agent_id) for FAN_OUT batching.
    levels = budget_topological_levels(dag)

    edges: list[Edge] = []
    entry_candidates: list[str] = []
    prev_level_last: list[str] | None = None

    for level_idx, level in enumerate(levels):
        # Batch into groups of size max_concurrent.
        batches: list[list[str]] = [level[i : i + max_concurrent] for i in range(0, len(level), max_concurrent)]
        level_outputs: list[str] = []

        for batch_idx, batch in enumerate(batches):
            batch_node_ids: list[str] = []
            fanout_id = f" f_l{level_idx}_b{batch_idx}".replace(" ", "")
            merge_id = f" merge_l{level_idx}_b{batch_idx}".replace(" ", "")
            needs_fanout_merge = len(batch) > 1

            if needs_fanout_merge:
                nodes[fanout_id] = NodeConfig(
                    id=fanout_id,
                    type=NodeType.FAN_OUT,
                    config={"branches": []},  # filled below
                )

            for task_id in batch:
                task = next(t for t in dag.tasks if t.id == task_id)
                node_id = f" task_{task_id}_{revision}".replace(" ", "")
                agent_meta = roster_map.get(task.agent_id)
                model = getattr(agent_meta, "model", None) if agent_meta else None
                if model is None and hasattr(agent_meta, "get"):
                    model = agent_meta.get("model")

                # Channel access: task reads its declared inputs and any
                # carryover; writes its expected_output. Carried-over task
                # outputs are also readable so the AGENT can consume them.
                input_keys = [ref for ref in task.inputs.values() if "." in ref]
                readable = sorted(
                    {"messages", "_ledger"}
                    | {f"{tid}.{out}" for tid, out in (tuple(r.split(".", 1)) for r in input_keys)}
                )

                nodes[node_id] = NodeConfig(
                    id=node_id,
                    type=NodeType.AGENT,
                    config={
                        "model": model,
                        "system_prompt": (
                            f"You are task '{task.id}' (expected_output: {task.expected_output}). Goal: {dag.goal}."
                        ),
                        "task_id": task.id,
                        "expected_output": task.expected_output,
                        "on_failure": task.on_failure,
                        "verify_after": task.verify is not None,
                        "channels": {
                            "readable": readable,
                            "writable": [
                                "messages",
                                f"{task.id}.{task.expected_output}",
                            ],
                        },
                    },
                )
                state[f"{task.id}.{task.expected_output}"] = ChannelDef(
                    type=ChannelType.LAST_VALUE,
                    default=None,
                )
                batch_node_ids.append(node_id)

                # Optional verifier sibling.
                if task.verify is not None:
                    verifier_id = f" verifier_{task.id}_{revision}".replace(" ", "")
                    verifier_meta = roster_map.get(task.verify.verifier_agent_id)
                    verifier_model = getattr(verifier_meta, "model", None) if verifier_meta else None
                    if verifier_model is None and hasattr(verifier_meta, "get"):
                        verifier_model = verifier_meta.get("model")
                    nodes[verifier_id] = NodeConfig(
                        id=verifier_id,
                        type=NodeType.AGENT,
                        config={
                            "model": verifier_model,
                            "system_prompt": task.verify.prompt,
                            "verifier_for": task.id,
                            "channels": {
                                "readable": [
                                    "messages",
                                    f"{task.id}.{task.expected_output}",
                                ],
                                "writable": [
                                    "messages",
                                    f"{task.id}.verified",
                                ],
                            },
                        },
                    )
                    state[f"{task.id}.verified"] = ChannelDef(
                        type=ChannelType.LAST_VALUE,
                        default=None,
                    )
                    edges.append(Edge(source=node_id, target=verifier_id))
                    batch_node_ids.append(verifier_id)

            if needs_fanout_merge:
                # Wire FAN_OUT branches → MERGE.
                fan_out_node = nodes[fanout_id]
                fan_out_node.config["branches"] = batch_node_ids
                nodes[merge_id] = NodeConfig(
                    id=merge_id,
                    type=NodeType.MERGE,
                    config={
                        "fan_out_source": fanout_id,
                        "output_channel": f"_merge_l{level_idx}_b{batch_idx}",
                    },
                )
                for bn in batch_node_ids:
                    edges.append(Edge(source=bn, target=fanout_id))
                edges.append(Edge(source=fanout_id, target=merge_id))
                level_outputs.append(merge_id)
            else:
                level_outputs.extend(batch_node_ids)

        if prev_level_last is None:
            entry_candidates = level_outputs
        else:
            for prev_id in prev_level_last:
                for next_id in level_outputs:
                    edges.append(Edge(source=prev_id, target=next_id))
        prev_level_last = level_outputs

    # Optional deterministic synthesis transform.
    transform_id: str | None = None
    if dag.synthesis_transform:
        transform_id = f" synth_transform_{revision}".replace(" ", "")
        nodes[transform_id] = NodeConfig(
            id=transform_id,
            type=NodeType.VARIABLE_SET,
            config={
                "variable_name": "_synthesis_buffer",
                "expression": dag.synthesis_transform,
                "transform_kind": "synthesis",
            },
        )

    synthesis_id = f" synthesis_{revision}".replace(" ", "")
    nodes[synthesis_id] = NodeConfig(
        id=synthesis_id,
        type=NodeType.AGENT,
        config={
            "model": "default",
            "system_prompt": dag.synthesis_prompt
            or f"Synthesize the per-task outputs into a final answer for: {dag.goal}",
            "synthesizer_for": dag.goal,
        },
    )

    # Wire last dependency level → (optional transform) → synthesis.
    if prev_level_last is not None:
        for prev_id in prev_level_last:
            if transform_id is not None:
                edges.append(Edge(source=prev_id, target=transform_id))
            else:
                edges.append(Edge(source=prev_id, target=synthesis_id))
    if transform_id is not None:
        edges.append(Edge(source=transform_id, target=synthesis_id))
    edges.append(Edge(source=synthesis_id, target="__end__"))

    entry = entry_candidates[0] if entry_candidates else synthesis_id

    return GraphConfig(
        version="1.0",
        name="dynamic-orchestration-executor",
        state=state,
        nodes=nodes,
        edges=edges,
        entry=entry,
    )
