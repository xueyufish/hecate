from __future__ import annotations

import logging

from hecate.engine.graph_dsl import GraphValidationError
from hecate.engine.types import (
    CompiledGraph,
    GraphConfig,
)

logger = logging.getLogger(__name__)


class GraphCompiler:
    """Compiles a GraphConfig into a validated CompiledGraph ready for execution."""

    def compile(self, config: GraphConfig) -> CompiledGraph:
        """Validate the graph structure and return a compiled graph."""
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
        if not config.entry:
            return
        if config.entry not in config.nodes:
            raise GraphValidationError(
                f"Entry point '{config.entry}' not found in nodes",
                field="entry",
            )

    def _validate_edges(self, config: GraphConfig) -> None:
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
        """Return node IDs not reachable from the entry point via BFS."""
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
