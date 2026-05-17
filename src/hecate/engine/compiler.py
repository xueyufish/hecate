from __future__ import annotations

from hecate.engine.graph_dsl import GraphValidationError
from hecate.engine.types import (
    ChannelDef,
    CompiledGraph,
    Edge,
    GraphConfig,
    NodeConfig,
)


class GraphCompiler:
    """Compiles a GraphConfig into a validated CompiledGraph ready for execution."""

    def compile(self, config: GraphConfig) -> CompiledGraph:
        """Validate the graph structure and return a compiled graph."""
        self._validate_entry(config)
        self._validate_edges(config)
        self._detect_unreachable(config)
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
        """Return a list of node IDs that are not reachable from the entry point."""
        reachable = {config.entry} if config.entry else set()
        for node_id in config.nodes:
            for edge in config.edges:
                src = edge.source
                tgt = edge.target if isinstance(edge.target, str) else ""
                if src == node_id:
                    reachable.add(node_id)
                if isinstance(edge.target, dict):
                    for t in edge.target.values():
                        if t == node_id:
                            reachable.add(node_id)
                elif tgt == node_id:
                    reachable.add(node_id)

        unreachable = [nid for nid in config.nodes if nid not in reachable]
        return unreachable
