"""Pluggable optimization passes for graph compilation.

Provides the abstract contract (OptimizationPass) and implementations:
- ``DeadNodeElimination`` — removes unreachable nodes
- ``ParallelBranchDetection`` — marks independent branches for parallel execution

OptimizationPass transforms a CompiledGraph after validation, preserving
semantics while improving performance or debuggability.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from hecate.engine.types import CompiledGraph, Edge


class OptimizationPass(ABC):
    """Abstract interface for graph optimization passes.

    An OptimizationPass takes a CompiledGraph and returns a new
    (potentially optimized) CompiledGraph. The original graph is
    never modified.
    """

    @abstractmethod
    def optimize(self, graph: CompiledGraph) -> CompiledGraph:
        """Optimize the graph and return a new CompiledGraph.

        Args:
            graph: The compiled graph to optimize.

        Returns:
            A new CompiledGraph (original is not modified).
        """
        ...


class DeadNodeElimination(OptimizationPass):
    """Remove nodes not reachable from the entry point.

    Uses BFS from the entry point to find all reachable nodes,
    then removes unreachable nodes and their associated edges.
    """

    def optimize(self, graph: CompiledGraph) -> CompiledGraph:
        """Remove unreachable nodes from the graph.

        Args:
            graph: The compiled graph to optimize.

        Returns:
            A new graph with unreachable nodes removed.
        """
        if not graph.entry_point:
            return graph

        reachable = self._find_reachable(graph)
        if len(reachable) == len(graph.nodes):
            return graph

        new_nodes = {nid: node for nid, node in graph.nodes.items() if nid in reachable}
        new_edges = [
            edge for edge in graph.edges if edge.source in reachable and self._edge_targets_reachable(edge, reachable)
        ]

        return CompiledGraph(
            nodes=new_nodes,
            edges=new_edges,
            channels=graph.channels,
            entry_point=graph.entry_point,
            name=graph.name,
        )

    def _find_reachable(self, graph: CompiledGraph) -> set[str]:
        """Find all nodes reachable from entry point via BFS."""
        adjacency: dict[str, list[str]] = {nid: [] for nid in graph.nodes}
        for edge in graph.edges:
            if isinstance(edge.target, str):
                if edge.source in adjacency and edge.target in graph.nodes:
                    adjacency[edge.source].append(edge.target)
            elif isinstance(edge.target, dict):
                for target_id in edge.target.values():
                    if edge.source in adjacency and target_id in graph.nodes:
                        adjacency[edge.source].append(target_id)

        visited: set[str] = set()
        queue = [graph.entry_point]
        while queue:
            node = queue.pop(0)
            if node in visited:
                continue
            visited.add(node)
            for neighbor in adjacency.get(node, []):
                if neighbor not in visited:
                    queue.append(neighbor)

        return visited

    def _edge_targets_reachable(self, edge: Edge, reachable: set[str]) -> bool:
        """Check if all edge targets are reachable."""
        if isinstance(edge.target, str):
            return edge.target in reachable or edge.target in ("__start__", "__end__")
        elif isinstance(edge.target, dict):
            return all(t in reachable or t in ("__start__", "__end__") for t in edge.target.values())
        return True


class ParallelBranchDetection(OptimizationPass):
    """Detect branches that can execute in parallel.

    Identifies nodes with multiple outgoing edges where the branches
    are independent (no shared descendants). Marks these as parallel
    groups in graph metadata.
    """

    def optimize(self, graph: CompiledGraph) -> CompiledGraph:
        """Detect and mark parallel branches.

        Args:
            graph: The compiled graph to analyze.

        Returns:
            A new graph with parallel_branches metadata.
        """
        adjacency: dict[str, list[str]] = {nid: [] for nid in graph.nodes}
        for edge in graph.edges:
            if isinstance(edge.target, str) and edge.target in graph.nodes:
                adjacency[edge.source].append(edge.target)
            elif isinstance(edge.target, dict):
                for target_id in edge.target.values():
                    if target_id in graph.nodes:
                        adjacency[edge.source].append(target_id)

        parallel_groups: list[list[str]] = []
        for _node_id, neighbors in adjacency.items():
            if len(neighbors) >= 2:
                independent = self._find_independent_branches(neighbors, adjacency)
                if len(independent) >= 2:
                    parallel_groups.append(independent)

        new_graph = CompiledGraph(
            nodes=graph.nodes,
            edges=graph.edges,
            channels=graph.channels,
            entry_point=graph.entry_point,
            name=graph.name,
        )
        new_graph.metadata["parallel_branches"] = parallel_groups
        return new_graph

    def _find_independent_branches(self, branches: list[str], adjacency: dict[str, list[str]]) -> list[str]:
        """Find branches that don't share descendants."""
        if len(branches) < 2:
            return branches

        descendants: dict[str, set[str]] = {}
        for branch in branches:
            descendants[branch] = self._get_descendants(branch, adjacency)

        independent: list[str] = []
        for i, branch in enumerate(branches):
            has_shared = False
            for j, other in enumerate(branches):
                if i != j and descendants[branch] & descendants[other]:
                    has_shared = True
                    break
            if not has_shared:
                independent.append(branch)

        return independent

    def _get_descendants(self, node: str, adjacency: dict[str, list[str]]) -> set[str]:
        """Get all descendants of a node via BFS."""
        visited: set[str] = set()
        queue = [node]
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            for neighbor in adjacency.get(current, []):
                if neighbor not in visited:
                    queue.append(neighbor)
        visited.discard(node)
        return visited
