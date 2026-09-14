"""Graph compilation pipeline: GraphConfig -> validation -> CompiledGraph.

The compiler takes a parsed GraphConfig and performs structural validation
(entry point existence, edge reference integrity, and reachability analysis)
before producing a CompiledGraph that is ready for execution by the Pregel runtime.
Unreachable nodes are logged as warnings but do not prevent compilation.
"""

from __future__ import annotations

import logging

from hecate.runtime.errors import GraphValidationError
from hecate.runtime.optimization import OptimizationPass
from hecate.runtime.types import (
    ChannelAccess,
    CompiledGraph,
    GraphConfig,
    NodeType,
    RoutingMode,
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
                SUGGESTION nodes and declarative interrupt lists.

        Returns:
            A CompiledGraph ready for execution.

        Raises:
            GraphValidationError: if entry, edge, or handoff cycle validation fails.
        """
        self._validate_entry(config)
        self._validate_edges(config)
        self._validate_handoff_edges(config)
        self._validate_fan_out_merge(config)
        self._validate_interrupt_lists(config)
        self._validate_execution_mode(config, execution_mode)
        self._validate_channel_access(config)
        self._validate_routing_config(config)
        self._validate_controller_config(config)
        self._validate_agent_invocation_mode(config)
        self._validate_agent_handoff_config(config)
        self._validate_accumulator_reducers(config)
        self._validate_fanout_configs(config)
        self._validate_node_cache_configs(config)
        unreachable = self._detect_unreachable(config)
        if unreachable:
            logger.warning("Unreachable nodes detected: %s", ", ".join(unreachable))
        channel_access = self._build_channel_access(config)
        graph = CompiledGraph(
            nodes=config.nodes,
            edges=config.edges,
            channels=config.state,
            entry_point=config.entry,
            name=config.name,
            channel_access=channel_access,
            interrupt_before=list(config.interrupt_before),
            interrupt_after=list(config.interrupt_after),
        )
        for optimization_pass in self._passes:
            graph = optimization_pass.optimize(graph)
        return graph

    def _validate_interrupt_lists(self, config: GraphConfig) -> None:
        """Validate that declarative interrupt lists reference declared nodes.

        Args:
            config: The parsed graph configuration.

        Raises:
            GraphValidationError: if either list references an undeclared node ID.
        """
        for list_name in ("interrupt_before", "interrupt_after"):
            for node_id in getattr(config, list_name):
                if node_id not in config.nodes:
                    raise GraphValidationError(
                        f"{list_name} references non-existent node '{node_id}'",
                        field=list_name,
                    )

    def _validate_channel_access(self, config: GraphConfig) -> None:
        """Warn when nodes declare channel access for channels not in graph state."""
        state_channels = set(config.state.keys())
        for node_id, node in config.nodes.items():
            channels = node.config.get("channels", {})
            for ch in channels.get("readable", []):
                if ch not in state_channels:
                    logger.warning(
                        "Node '%s' declares readable channel '%s' which is not defined in graph state",
                        node_id,
                        ch,
                    )
            for ch in channels.get("writable", []):
                if ch not in state_channels:
                    logger.warning(
                        "Node '%s' declares writable channel '%s' which is not defined in graph state",
                        node_id,
                        ch,
                    )

    def _validate_routing_config(self, config: GraphConfig) -> None:
        """Validate routing configuration for CONDITION nodes with advanced routing modes."""
        from hecate.runtime.types import NodeType

        for node_id, node in config.nodes.items():
            if node.type != NodeType.CONDITION:
                continue
            routing_mode = node.config.get("routing_mode")
            if not routing_mode or routing_mode == RoutingMode.CONDITION:
                continue
            routing_config = node.config.get("routing_config", {})

            if routing_mode == RoutingMode.INTENT:
                intent_patterns = routing_config.get("intent_patterns", [])
                intent_package = routing_config.get("intent_package")
                # 6.23: package-backed form (intent_package + category_targets)
                # or the legacy form (intent_patterns) — exactly one is required.
                if not intent_patterns and not intent_package:
                    raise GraphValidationError(
                        f"CONDITION node '{node_id}' with routing_mode='intent' "
                        f"requires routing_config.intent_patterns or routing_config.intent_package",
                        field=f"nodes[{node_id}].config.routing_config",
                    )
                if intent_package and not routing_config.get("category_targets"):
                    raise GraphValidationError(
                        f"CONDITION node '{node_id}' with package-backed intent routing "
                        f"requires routing_config.category_targets",
                        field=f"nodes[{node_id}].config.routing_config",
                    )

            elif routing_mode == RoutingMode.DYNAMIC:
                candidate_agents = routing_config.get("candidate_agents", [])
                if not candidate_agents:
                    raise GraphValidationError(
                        f"CONDITION node '{node_id}' with routing_mode='dynamic' "
                        f"requires routing_config.candidate_agents",
                        field=f"nodes[{node_id}].config.routing_config",
                    )
                node_ids = set(config.nodes.keys())
                for candidate in candidate_agents:
                    if candidate not in node_ids:
                        raise GraphValidationError(
                            f"CONDITION node '{node_id}' candidate_agent '{candidate}' is not a declared node",
                            field=f"nodes[{node_id}].config.routing_config.candidate_agents",
                        )

    def _validate_controller_config(self, config: GraphConfig) -> None:
        """Validate shape-level configuration for CONTROLLER nodes (2.6a).

        Shape only: the studio save path validates package/category
        *existence* (it owns DB access). Here: required fields, non-empty
        mapping, and route targets referencing declared nodes.

        Raises:
            GraphValidationError: on missing defaults, empty mappings, or
                undeclared route targets.
        """
        for node_id, node in config.nodes.items():
            if node.type != NodeType.CONTROLLER:
                continue
            category_targets = node.config.get("category_targets") or {}
            if not category_targets:
                raise GraphValidationError(
                    f"CONTROLLER node '{node_id}' requires non-empty category_targets",
                    field=f"nodes[{node_id}].config.category_targets",
                )
            default_workflow = node.config.get("default_workflow")
            if not default_workflow:
                raise GraphValidationError(
                    f"CONTROLLER node '{node_id}' requires a default_workflow target",
                    field=f"nodes[{node_id}].config.default_workflow",
                )
            if not node.config.get("intent_package"):
                raise GraphValidationError(
                    f"CONTROLLER node '{node_id}' requires an intent_package reference",
                    field=f"nodes[{node_id}].config.intent_package",
                )
            node_ids = set(config.nodes.keys())
            targets = dict(category_targets)
            for role in ("start_workflow", "default_workflow", "end_workflow"):
                target = node.config.get(role)
                if target:
                    targets[role] = target
            for role, target in targets.items():
                if target not in node_ids:
                    raise GraphValidationError(
                        f"CONTROLLER node '{node_id}' target '{target}' ({role}) is not a declared node",
                        field=f"nodes[{node_id}].config.category_targets",
                    )

    def _validate_agent_invocation_mode(self, config: GraphConfig) -> None:
        """Validate invocation_mode field on AGENT nodes.

        When present, invocation_mode must be 'direct' or 'tool'.
        Defaults to 'direct' when absent (handled at runtime).

        Raises:
            GraphValidationError: if invocation_mode is not a valid value.
        """
        valid_modes = {"direct", "tool"}
        for node_id, node in config.nodes.items():
            if node.type.value != "agent":
                continue
            invocation_mode = node.config.get("invocation_mode")
            if invocation_mode is not None and invocation_mode not in valid_modes:
                raise GraphValidationError(
                    f"AGENT node '{node_id}' has invalid invocation_mode '{invocation_mode}'. "
                    f"Must be one of: {', '.join(sorted(valid_modes))}",
                    field=f"nodes[{node_id}].config.invocation_mode",
                )

    def _validate_agent_handoff_config(self, config: GraphConfig) -> None:
        """Validate handoff.context_mode field on AGENT nodes.

        When present, context_mode must be 'inherited', 'isolated', or 'summarized'.
        Defaults to 'inherited' when absent (handled at runtime).

        Raises:
            GraphValidationError: if context_mode is not a valid value.
        """
        valid_modes = {"inherited", "isolated", "summarized"}
        for node_id, node in config.nodes.items():
            if node.type.value != "agent":
                continue
            handoff_cfg = node.config.get("handoff")
            if handoff_cfg is None:
                continue
            context_mode = handoff_cfg.get("context_mode")
            if context_mode is not None and context_mode not in valid_modes:
                raise GraphValidationError(
                    f"AGENT node '{node_id}' has invalid handoff.context_mode '{context_mode}'. "
                    f"Must be one of: {', '.join(sorted(valid_modes))}",
                    field=f"nodes[{node_id}].config.handoff.context_mode",
                )

    def _validate_accumulator_reducers(self, config: GraphConfig) -> None:
        """Validate ACCUMULATOR channels name registered reducers (1.3.21③).

        Channels with ``reduce_fn is None`` keep the legacy overwrite semantics;
        channels with a non-None name must resolve to a registered reducer or
        compilation fails (the silent-overwrite fallback is removed).

        Raises:
            GraphValidationError: if any ACCUMULATOR channel names an
                unregistered reducer.
        """
        from hecate.runtime.channel import UnknownReducerError, get_reducer

        for name, ch_def in config.state.items():
            if ch_def.type.value != "accumulator":
                continue
            if ch_def.reduce_fn is None:
                continue
            try:
                get_reducer(ch_def.reduce_fn)
            except UnknownReducerError as exc:
                raise GraphValidationError(
                    f"ACCUMULATOR channel '{name}' references unknown reducer '{ch_def.reduce_fn}': {exc}",
                    field=f"state[{name}].reduce",
                ) from exc

    def _validate_fanout_configs(self, config: GraphConfig) -> None:
        """Validate ``fanout`` configuration on CONDITION nodes (1.3.21③).

        Required fields, target existence, and max_fanout shape are all
        checked here so runtime errors stay limited to data-shape problems
        (over channel emptiness, target node type mismatch).

        Raises:
            GraphValidationError: if a fanout declaration is malformed.
        """
        from hecate.runtime.types import NodeType

        node_ids = set(config.nodes.keys())
        state_channels = set(config.state.keys())
        for node_id, node in config.nodes.items():
            fanout = node.config.get("fanout")
            if fanout is None:
                continue
            if node.type != NodeType.CONDITION:
                raise GraphValidationError(
                    f"Node '{node_id}' declares fanout but type is '{node.type.value}'; "
                    "fanout is only allowed on CONDITION nodes",
                    field=f"nodes[{node_id}].config.fanout",
                )
            if not isinstance(fanout, dict):
                raise GraphValidationError(
                    f"fanout config on '{node_id}' must be an object, got {type(fanout).__name__}",
                    field=f"nodes[{node_id}].config.fanout",
                )
            over = fanout.get("over")
            target = fanout.get("target")
            state_key = fanout.get("state_key")
            missing = [k for k in ("over", "target", "state_key") if not fanout.get(k)]
            if missing:
                raise GraphValidationError(
                    f"fanout config on '{node_id}' missing required field(s): {', '.join(missing)}",
                    field=f"nodes[{node_id}].config.fanout",
                )
            if over not in state_channels:
                raise GraphValidationError(
                    f"fanout.over '{over}' on '{node_id}' is not declared in graph state",
                    field=f"nodes[{node_id}].config.fanout.over",
                )
            if target not in node_ids:
                raise GraphValidationError(
                    f"fanout.target '{target}' on '{node_id}' is not a declared node",
                    field=f"nodes[{node_id}].config.fanout.target",
                )
            if not isinstance(state_key, str) or not state_key:
                raise GraphValidationError(
                    f"fanout.state_key on '{node_id}' must be a non-empty string",
                    field=f"nodes[{node_id}].config.fanout.state_key",
                )
            max_fanout = fanout.get("max_fanout")
            if max_fanout is not None and (
                not isinstance(max_fanout, int) or isinstance(max_fanout, bool) or max_fanout <= 0
            ):
                raise GraphValidationError(
                    f"fanout.max_fanout on '{node_id}' must be a positive integer, got {max_fanout!r}",
                    field=f"nodes[{node_id}].config.fanout.max_fanout",
                )

    def _validate_node_cache_configs(self, config: GraphConfig) -> None:
        """Validate ``cache`` blocks on nodes (1.3.21IV).

        The JSON schema rejects malformed blocks at parse time; this stage
        covers GraphConfig construction that bypassed the schema (shape,
        ttl positivity, scope value) and the registry-dependent check the
        schema cannot do: ``key_func`` must name a registered function.

        Raises:
            GraphValidationError: if a cache declaration is malformed or
                references an unregistered key function.
        """
        from hecate.runtime.node_cache import CachePolicy, UnknownKeyFuncError, get_node_key_func

        for node_id, node in config.nodes.items():
            raw = node.config.get("cache")
            if raw is None:
                continue
            if not isinstance(raw, dict):
                raise GraphValidationError(
                    f"cache config on '{node_id}' must be an object, got {type(raw).__name__}",
                    field=f"nodes[{node_id}].config.cache",
                )
            try:
                policy = CachePolicy.from_config(raw)
            except (KeyError, TypeError, ValueError) as exc:
                raise GraphValidationError(
                    f"invalid cache config on '{node_id}': {exc} "
                    "(ttl is required and must be a positive integer; "
                    "scope must be 'session' or 'tenant')",
                    field=f"nodes[{node_id}].config.cache",
                ) from exc
            if policy.ttl <= 0:
                raise GraphValidationError(
                    f"cache.ttl on '{node_id}' must be a positive integer, got {policy.ttl!r}",
                    field=f"nodes[{node_id}].config.cache.ttl",
                )
            if policy.scope not in ("session", "tenant"):
                raise GraphValidationError(
                    f"cache.scope on '{node_id}' must be 'session' or 'tenant', got {policy.scope!r}",
                    field=f"nodes[{node_id}].config.cache.scope",
                )
            if policy.key_func is not None:
                try:
                    get_node_key_func(policy.key_func)
                except UnknownKeyFuncError as exc:
                    raise GraphValidationError(
                        f"cache.key_func on '{node_id}' references unknown key function '{policy.key_func}': {exc}",
                        field=f"nodes[{node_id}].config.cache.key_func",
                    ) from exc

    def _build_channel_access(self, config: GraphConfig) -> dict[str, ChannelAccess]:
        """Build per-node channel access map from node configurations."""
        result: dict[str, ChannelAccess] = {}
        for node_id, node in config.nodes.items():
            channels = node.config.get("channels", {})
            result[node_id] = ChannelAccess(
                readable=set(channels.get("readable", [])),
                writable=set(channels.get("writable", [])),
            )
        return result

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
        from hecate.runtime.types import NodeType

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
        user presence) and declarative interrupt lists (pausing requires
        checkpointing, which task mode does not provide). Conversational
        mode allows all node types and both lists.

        Args:
            config: The parsed graph configuration.
            execution_mode: "conversational" or "task".

        Raises:
            GraphValidationError: if task mode contains forbidden node types
                or non-empty interrupt lists.
        """
        if execution_mode != "task":
            return

        from hecate.runtime.types import NodeType

        forbidden = {NodeType.SUGGESTION}
        for node_id, node in config.nodes.items():
            if node.type in forbidden:
                raise GraphValidationError(
                    f"{node.type.value} nodes are forbidden in task mode workflows",
                    field=f"nodes[{node_id}]",
                )

        for list_name in ("interrupt_before", "interrupt_after"):
            if getattr(config, list_name):
                raise GraphValidationError(
                    f"declarative interrupts are forbidden in task mode ({list_name} is non-empty)",
                    field=list_name,
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
        from hecate.runtime.types import NodeType

        handoff_edges = [e for e in config.edges if e.trigger in ("handoff", "dynamic_handoff")]
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
