"""Graph compilation pipeline: GraphConfig -> validation -> CompiledGraph.

The compiler takes a parsed GraphConfig and performs structural validation
(entry point existence, edge reference integrity, and reachability analysis)
before producing a CompiledGraph that is ready for execution by the Pregel runtime.
Unreachable nodes are logged as warnings but do not prevent compilation.
"""

from __future__ import annotations

import logging

from hecate.engine.graph_dsl import GraphValidationError
from hecate.engine.optimization import OptimizationPass
from hecate.engine.types import (
    CompiledGraph,
    GraphConfig,
)

logger = logging.getLogger(__name__)


class GraphCompiler:
    """Compiles a GraphConfig into a validated CompiledGraph ready for execution.

    The compilation pipeline consists of three validation stages followed by
    CompiledGraph construction:

    1. **Entry validation** -- ensures the declared entry point references a real node.
    2. **Edge validation** -- ensures every edge source and target references a
       real node or a sentinel (``__start__``, ``__end__``).
    3. **Reachability analysis** -- performs a BFS from the entry point and warns
       about any nodes that cannot be reached.

    The compiler intentionally does **not** reject graphs with unreachable nodes.
    This allows graphs to be incrementally built and tested during development.
    """

    def __init__(self, passes: list[OptimizationPass] | None = None) -> None:
        """Initialize the compiler with optional optimization passes.

        Args:
            passes: Optimization passes to apply after validation. Applied in list order.
                Defaults to empty list (no optimization).
        """
        self._passes: list[OptimizationPass] = passes or []

    def compile(self, config: GraphConfig, execution_mode: str = "conversational") -> CompiledGraph:
        """Validate the graph structure and return a compiled graph.

        Args:
            config: The parsed graph configuration to compile.
            execution_mode: "conversational" or "task". Task mode forbids
                SUGGESTION nodes.

        Returns:
            A CompiledGraph ready for execution.

        Raises:
            GraphValidationError: if entry, edge, or handoff cycle validation fails.
        """
        self._validate_entry(config)
        self._validate_edges(config)
        self._validate_handoff_edges(config)
        self._validate_fan_out_merge(config)
        self._validate_execution_mode(config, execution_mode)
        unreachable = self._detect_unreachable(config)
        if unreachable:
            logger.warning("Unreachable nodes detected: %s", ", ".join(unreachable))
        graph = CompiledGraph(
            nodes=config.nodes,
            edges=config.edges,
            channels=config.state,
            entry_point=config.entry,
            name=config.name,
        )
        for optimization_pass in self._passes:
            graph = optimization_pass.optimize(graph)
        return graph

    def _validate_entry(self, config: GraphConfig) -> None:
        """Ensure the declared entry point references an existing node.

        Silently returns if no entry point is declared (empty-string entry is
        allowed for graphs that are started via explicit node selection).

        Raises:
            GraphValidationError: if the entry point is declared but not found.
        """
        if not config.entry:
            return
        if config.entry not in config.nodes:
            raise GraphValidationError(
                f"Entry point '{config.entry}' not found in nodes",
                field="entry",
            )

    def _validate_edges(self, config: GraphConfig) -> None:
        """Ensure every edge source and target references a valid node ID.

        Sentinel node IDs ``__start__`` and ``__end__`` are treated as valid
        targets/sources in addition to declared node IDs. Conditional edges
        (dict-valued targets) have each branch validated independently.

        Raises:
            GraphValidationError: if any edge source or target is invalid.
        """
        node_ids = set(config.nodes.keys()) | {"__start__", "__end__"}
        for edge in config.edges:
            if edge.source not in node_ids:
                raise GraphValidationError(
                    f"Edge source '{edge.source}' references non-existent node",
                    field=f"edges[{edge.source}]",
                )
            if isinstance(edge.target, str):
                if edge.target not in node_ids:
                    raise GraphValidationError(
                        f"Edge target '{edge.target}' references non-existent node",
                        field=f"edges[{edge.source}].target",
                    )
            elif isinstance(edge.target, dict):
                for key, target_id in edge.target.items():
                    if target_id not in node_ids:
                        raise GraphValidationError(
                            f"Edge target '{target_id}' (key '{key}') references non-existent node",
                            field=f"edges[{edge.source}].target.{key}",
                        )

    def _validate_fan_out_merge(self, config: GraphConfig) -> None:
        """Validate FAN_OUT/MERGE structural constraints.

        Every FAN_OUT node must have at least one reachable MERGE node downstream,
        and every MERGE node must have an upstream FAN_OUT node.

        Raises:
            GraphValidationError: if FAN_OUT/MERGE constraints are violated.
        """
        from hecate.engine.types import NodeType

        fan_out_nodes = [nid for nid, n in config.nodes.items() if n.type == NodeType.FAN_OUT]
        merge_nodes = [nid for nid, n in config.nodes.items() if n.type == NodeType.MERGE]

        if not fan_out_nodes and not merge_nodes:
            return

        adjacency: dict[str, list[str]] = {nid: [] for nid in config.nodes}
        for edge in config.edges:
            if isinstance(edge.target, str):
                if edge.source in adjacency and edge.target in config.nodes:
                    adjacency[edge.source].append(edge.target)
            elif isinstance(edge.target, dict):
                for target_id in edge.target.values():
                    if edge.source in adjacency and target_id in config.nodes:
                        adjacency[edge.source].append(target_id)

        def bfs_reachable(start: str) -> set[str]:
            visited: set[str] = set()
            queue = [start]
            while queue:
                node = queue.pop(0)
                if node in visited:
                    continue
                visited.add(node)
                for neighbor in adjacency.get(node, []):
                    if neighbor not in visited:
                        queue.append(neighbor)
            return visited

        for fan_out_id in fan_out_nodes:
            reachable = bfs_reachable(fan_out_id)
            merge_reachable = reachable & set(merge_nodes)
            if not merge_reachable:
                raise GraphValidationError(
                    f"FAN_OUT node '{fan_out_id}' has no reachable MERGE node",
                    field=f"nodes[{fan_out_id}]",
                )

        for merge_id in merge_nodes:
            has_upstream = False
            for fan_out_id in fan_out_nodes:
                reachable = bfs_reachable(fan_out_id)
                if merge_id in reachable:
                    has_upstream = True
                    break
            if not has_upstream:
                raise GraphValidationError(
                    f"MERGE node '{merge_id}' has no upstream FAN_OUT node",
                    field=f"nodes[{merge_id}]",
                )

    def _validate_execution_mode(self, config: GraphConfig, execution_mode: str) -> None:
        """Validate node restrictions based on execution mode.

        Task mode forbids SUGGESTION nodes (interaction nodes that require
        user presence). Conversational mode allows all node types.

        Args:
            config: The parsed graph configuration.
            execution_mode: "conversational" or "task".

        Raises:
            GraphValidationError: if task mode contains forbidden node types.
        """
        if execution_mode != "task":
            return

        from hecate.engine.types import NodeType

        forbidden = {NodeType.SUGGESTION}
        for node_id, node in config.nodes.items():
            if node.type in forbidden:
                raise GraphValidationError(
                    f"{node.type.value} nodes are forbidden in task mode workflows",
                    field=f"nodes[{node_id}]",
                )

    def _detect_unreachable(self, config: GraphConfig) -> list[str]:
        """Return node IDs not reachable from the entry point via BFS.

        Performs a breadth-first traversal over the edge graph starting from
        the declared entry point. A node is considered "unreachable" if there
        is no path of edges from the entry point to that node. Conditional
        edges (dict-valued targets) are conservatively treated as if all
        branches are taken, so all dict values are included as neighbors.

        Returns:
            A list of node IDs that cannot be reached. Empty if the graph
            is fully connected or has no entry point.
        """
        if not config.entry:
            return []

        adjacency: dict[str, list[str]] = {nid: [] for nid in config.nodes}
        for edge in config.edges:
            if isinstance(edge.target, str):
                if edge.source in adjacency and edge.target in config.nodes:
                    adjacency[edge.source].append(edge.target)
            elif isinstance(edge.target, dict):
                for target_id in edge.target.values():
                    if edge.source in adjacency and target_id in config.nodes:
                        adjacency[edge.source].append(target_id)

        visited: set[str] = set()
        queue = [config.entry]
        while queue:
            node = queue.pop(0)
            if node in visited:
                continue
            visited.add(node)
            for neighbor in adjacency.get(node, []):
                if neighbor not in visited:
                    queue.append(neighbor)

        return [nid for nid in config.nodes if nid not in visited]

    def _validate_handoff_edges(self, config: GraphConfig) -> None:
        """Validate handoff edges: source and target must be agent nodes, no cycles.

        Handoff edges (trigger="handoff") represent control transfer between
        agents. This method validates two constraints:

        1. Both source and target of a handoff edge must be agent-type nodes.
        2. The handoff subgraph must not contain cycles (A→B→C→A is rejected).

        Raises:
            GraphValidationError: if a handoff edge violates these constraints.
        """
        from hecate.engine.types import NodeType

        handoff_edges = [e for e in config.edges if e.trigger == "handoff"]
        if not handoff_edges:
            return

        for edge in handoff_edges:
            source_node = config.nodes.get(edge.source)
            if source_node and source_node.type != NodeType.AGENT:
                raise GraphValidationError(
                    f"Handoff edge source '{edge.source}' must be an agent node, got '{source_node.type.value}'",
                    field=f"edges[{edge.source}]",
                )

            targets = [edge.target] if isinstance(edge.target, str) else list(edge.target.values())
            for target_id in targets:
                if target_id in ("__start__", "__end__"):
                    continue
                target_node = config.nodes.get(target_id)
                if target_node and target_node.type != NodeType.AGENT:
                    raise GraphValidationError(
                        f"Handoff edge target '{target_id}' must be an agent node, got '{target_node.type.value}'",
                        field=f"edges[{edge.source}].target",
                    )

        adjacency: dict[str, list[str]] = {}
        for edge in handoff_edges:
            if edge.source not in adjacency:
                adjacency[edge.source] = []
            targets = [edge.target] if isinstance(edge.target, str) else list(edge.target.values())
            for target_id in targets:
                if target_id not in ("__start__", "__end__"):
                    adjacency[edge.source].append(target_id)

        visited: set[str] = set()
        path: set[str] = set()

        def has_cycle(node: str) -> bool:
            visited.add(node)
            path.add(node)
            for neighbor in adjacency.get(node, []):
                if neighbor in path:
                    return True
                if neighbor not in visited and has_cycle(neighbor):
                    return True
            path.discard(node)
            return False

        for node in adjacency:
            if node not in visited and has_cycle(node):
                raise GraphValidationError(
                    "Circular handoff chain detected in graph. Handoff edges must not form cycles.",
                    field="edges",
                )
