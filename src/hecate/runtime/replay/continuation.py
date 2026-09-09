"""Log-derived continuation resolution for time-travel resume (1.3.21②).

Pure ``(events, graph, folded_state) → next node set`` functions. The fold
machine rebuilds *state* at any version; this module rebuilds *control flow*
at any commit point — which nodes the engine should dispatch next when
execution continues from that point.

Anchor precedence follows log order: the last commit point that is not a
state-mutation closer (``STEP_END`` with ``source="update_state"``) decides:

* unclosed ``INTERRUPT`` — the 1.3.21① phase-aware descriptor rules:
  declarative ``before`` runs the paused nodes themselves, ``after`` /
  worker-authored interrupts follow the out-edges of the interrupting nodes;
* ``FORK`` — the ``next_nodes`` captured at fork time;
* ``STEP_END`` — the out-edges of the nodes completed since the previous
  commit point (tail crash recovery);
* no commit point at all — the graph entry point.

The module never touches engine state or the checkpoint cache; callers pass
the folded channel snapshot so conditional edges resolve against the same
``_route`` value the live engine would see.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from hecate.runtime.eventstore import Event, EventType

if TYPE_CHECKING:
    from hecate.runtime.types import CompiledGraph

_COMMIT_POINT_TYPES = frozenset({EventType.STEP_END, EventType.INTERRUPT, EventType.FORK})


@dataclass
class Continuation:
    """Resolved continuation for an execution restart point.

    ``skip_before`` mirrors the ① one-shot exemption: when the anchor is a
    declarative ``before`` pause, the paused nodes must run once without
    re-triggering their own ``interrupt_before`` breakpoint.
    """

    nodes: list[str] = field(default_factory=list)
    skip_before: set[str] = field(default_factory=set)


def _etype(event: Event) -> str:
    return event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type)


def resolve_out_edges(graph: CompiledGraph, sources: list[str], route_value: str = "true") -> list[str] | None:
    """Union of out-edges of ``sources``; conditional edges resolve via ``route_value``.

    Same semantics as the engine's post-superstep edge resolution: string
    targets dispatch directly, dict targets look up the route key (falling
    back to ``"default"``). Returns ``None`` when the edges terminate at
    ``__end__`` (graph complete — callers must NOT fall back to the entry
    point), an empty list when no edges exist at all (dead end — entry
    fallback applies, mirroring the ① interrupt resolution).
    """
    resolved: list[str] = []
    for source in sources:
        for edge in graph.edges:
            if edge.source != source:
                continue
            if isinstance(edge.target, str):
                resolved.append(edge.target)
            elif isinstance(edge.target, dict):
                target = edge.target.get(route_value, edge.target.get("default"))
                if target:
                    resolved.append(target)
    if "__end__" in resolved:
        return None
    return list(dict.fromkeys(resolved))


def _interrupt_continuation(
    descriptor: dict, node_id: str | None, graph: CompiledGraph, route_value: str
) -> Continuation:
    """Apply the ① phase-aware interrupt rules to a log-loaded descriptor."""
    nodes = [n for n in descriptor.get("nodes", []) if n]
    if not nodes and node_id:
        nodes = [node_id]
    if descriptor.get("kind") == "declarative" and descriptor.get("phase") == "before":
        # A before-pause never ran its superstep: the paused nodes execute
        # themselves, once, without re-triggering their own breakpoint.
        return Continuation(nodes=list(dict.fromkeys(nodes)), skip_before=set(nodes))
    resolved = resolve_out_edges(graph, nodes, route_value)
    if resolved is None:
        return Continuation()
    if resolved:
        return Continuation(nodes=resolved)
    if graph.entry_point:
        return Continuation(nodes=[graph.entry_point])
    return Continuation()


def derive_continuation(
    events: list[Event],
    graph: CompiledGraph,
    channel_state: dict[str, Any],
) -> Continuation:
    """Derive the continuation node set for the last usable commit point.

    Args:
        events: Session events (already sliced to the target version).
        graph: The compiled graph the continuation dispatches against.
        channel_state: Folded channel snapshot at the target version; the
            ``_route`` value from it arbitrates conditional edges.

    Returns:
        The resolved :class:`Continuation`.
    """
    route_value = str(channel_state.get("_route", "true"))

    # Forward scan collecting anchors: (type, payload, node_id, executed nodes
    # since the previous commit point). NODE_END events precede their step's
    # commit event, so each anchor captures exactly the nodes it closed out.
    anchors: list[tuple[str, dict, str | None, list[str]]] = []
    executed: list[str] = []
    for event in events:
        etype = _etype(event)
        if etype == EventType.NODE_END:
            if event.node_id:
                executed.append(event.node_id)
            continue
        if etype in _COMMIT_POINT_TYPES:
            payload = dict(event.payload or {})
            anchors.append((etype, payload, event.node_id, list(executed)))
            executed = []

    for etype, payload, node_id, executed_nodes in reversed(anchors):
        if etype == EventType.STEP_END and payload.get("source") == "update_state":
            # State-mutation closer: not execution progress — keep looking.
            continue
        if etype == EventType.FORK:
            return Continuation(nodes=[n for n in payload.get("next_nodes", []) if n])
        if etype == EventType.INTERRUPT:
            if not payload.get("nodes") and node_id:
                payload["nodes"] = [node_id]
            return _interrupt_continuation(payload, node_id, graph, route_value)
        # STEP_END at tail: continue from the completed nodes' out-edges.
        resolved = resolve_out_edges(graph, executed_nodes, route_value)
        if resolved is None:
            return Continuation()
        if resolved:
            return Continuation(nodes=resolved)
        if graph.entry_point:
            return Continuation(nodes=[graph.entry_point])
        return Continuation()

    if graph.entry_point:
        return Continuation(nodes=[graph.entry_point])
    return Continuation()
