"""Graph compilation pipeline: GraphConfig -> validation -> CompiledGraph.

The compiler takes a parsed GraphConfig and performs structural validation
(entry point existence, edge reference integrity, and reachability analysis)
before producing a CompiledGraph that is ready for execution by the Pregel runtime.
Unreachable nodes are logged as warnings but do not prevent compilation.
"""

from __future__ import annotations

import logging

from hecate.engine.graph_dsl import GraphValidationError
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

    def compile(self, config: GraphConfig) -> CompiledGraph:
        """Validate the graph structure and return a compiled graph.

        Args:
            config: The parsed graph configuration to compile.

        Returns:
            A CompiledGraph ready for execution.

        Raises:
            GraphValidationError: if entry or edge validation fails.
        """
        self._validate_entry(config)
        self._validate_edges(config)
        unreachable = self._detect_unreachable(config)
        if unreachable:
            logger.warning("Unreachable nodes detected: %s", ", ".join(unreachable))
        return CompiledGraph(
            nodes=config.nodes,
            edges=config.edges,
            channels=config.state,
            entry_point=config.entry,
            name=config.name,
        )

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
