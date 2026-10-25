import type { Node, Edge } from "@xyflow/react";
import type { NodeConfig } from "./workflow-types";

/** React Flow node data type */
export interface FlowNodeData extends Record<string, unknown> {
  label: string;
  type: string;
  config: NodeConfig;
}

export type FlowNode = Node<FlowNodeData>;

/** Internal representation of parsed Graph DSL (loosely typed for bridge use) */
interface DslNodeDef {
  type: string;
  config: NodeConfig;
}

interface DslEdgeDef {
  source: string;
  target: string | Record<string, string>;
  trigger?: string | null;
  type?: "handoff" | null;
}

interface ParsedDsl {
  name: string;
  nodes: Record<string, DslNodeDef>;
  edges: DslEdgeDef[];
  entry?: string;
}

const NODE_TYPE_LABELS: Record<string, string> = {
  conversation: "Conversation",
  "tool-call": "Tool Call",
  condition: "Condition",
  agent: "Agent",
  "knowledge-retrieval": "Knowledge Retrieval",
  "variable-set": "Variable Set",
  "fan-out": "Fan Out",
  merge: "Merge",
  suggestion: "Suggestion",
  controller: "Controller",
};

/** Short human-readable label for a canvas node. */
function nodeLabel(id: string, nodeDef: DslNodeDef): string {
  if (nodeDef.config.name) return nodeDef.config.name;
  const prompt = nodeDef.config.system_prompt;
  if (prompt) {
    const oneLine = prompt.replace(/\s+/g, " ").trim();
    return oneLine.length > 24 ? `${oneLine.slice(0, 24)}…` : oneLine;
  }
  return NODE_TYPE_LABELS[nodeDef.type] || id;
}

/** Convert Graph DSL to React Flow nodes and edges */
export function dslToReactFlow(dsl: ParsedDsl): {
  nodes: FlowNode[];
  edges: Edge[];
} {
  const nodeIds = Object.keys(dsl.nodes);
  // Default layout: BFS depth from the entry node → left-to-right columns,
  // so a pipeline reads in execution order without manual arranging.
  const depth = new Map<string, number>();
  const entryId = dsl.entry && dsl.nodes[dsl.entry] ? dsl.entry : nodeIds[0];
  const queue = [entryId];
  depth.set(entryId, 0);
  while (queue.length > 0) {
    const cur = queue.shift()!;
    for (const e of dsl.edges) {
      if (e.source !== cur) continue;
      const targets = typeof e.target === "string" ? [e.target] : Object.values(e.target);
      for (const t of targets) {
        if (dsl.nodes[t] && !depth.has(t)) {
          depth.set(t, (depth.get(cur) ?? 0) + 1);
          queue.push(t);
        }
      }
    }
  }
  const maxDepth = Math.max(0, ...depth.values());
  for (const id of nodeIds) {
    if (!depth.has(id)) depth.set(id, maxDepth + 1);
  }
  const layerSeen = new Map<number, number>();
  const nodes: FlowNode[] = nodeIds.map((id) => {
    const nodeDef = dsl.nodes[id];
    const d = depth.get(id) ?? 0;
    const row = layerSeen.get(d) ?? 0;
    layerSeen.set(d, row + 1);
    return {
      id,
      type: nodeDef.type,
      position: {
        x: 280 * d,
        y: 140 * row,
      },
      // Explicit dimensions mark nodes as initialized immediately, so edges
      // render without waiting for ResizeObserver measurement (which stalls
      // in embedded webviews).
      width: 180,
      height: 48,
      data: {
        // Canvas label: explicit name wins; otherwise fall back to a short
        // prompt excerpt so long system prompts don't flood the canvas. The
        // full prompt stays editable in the config panel.
        label: nodeLabel(id, nodeDef),
        type: nodeDef.type,
        config: nodeDef.config,
      },
    };
  });

  const edges: Edge[] = dsl.edges.flatMap((edgeDef, index) => {
    const source = edgeDef.source;
    const target = edgeDef.target;
    const isHandoff = edgeDef.trigger === "handoff" || edgeDef.type === "handoff";

    if (typeof target === "string") {
      return [{
        id: `e-${index}`,
        source,
        target,
        animated: true as const,
        style: isHandoff ? { stroke: "#8b5cf6", strokeWidth: 2, strokeDasharray: "5 5" } : undefined,
        label: isHandoff ? "Handoff" : "",
        data: { edgeType: isHandoff ? "handoff" : "default" },
      }];
    }

    // Conditional edge: create one edge per route
    return Object.entries(target).map(([route, targetId], routeIndex) => ({
      id: `e-${index}-${routeIndex}`,
      source,
      target: targetId,
      label: route,
      animated: true,
    }));
  });

  return { nodes, edges };
}

/** Convert React Flow nodes and edges back to Graph DSL */
export function reactFlowToDsl(
  nodes: Node[],
  edges: Edge[],
  name: string
) {
  const dslNodes: Record<string, DslNodeDef> = {};
  for (const node of nodes) {
    const data = node.data as FlowNodeData | undefined;
    dslNodes[node.id] = {
      type: node.type || "conversation",
      config: data?.config || {},
    };
  }

  // Merge conditional edges back into dict targets
  const edgeGroups = new Map<string, Map<string, string>>();
  const simpleEdges: { source: string; target: string }[] = [];

  for (const edge of edges) {
    if (edge.label && typeof edge.label === "string" && edge.label !== "Handoff") {
      if (!edgeGroups.has(edge.source)) {
        edgeGroups.set(edge.source, new Map());
      }
      edgeGroups.get(edge.source)!.set(edge.label, edge.target);
    } else {
      simpleEdges.push({ source: edge.source, target: edge.target });
    }
  }

  const dslEdges: DslEdgeDef[] = [];

  for (const { source, target } of simpleEdges) {
    if (edgeGroups.has(source)) continue;
    const flowEdge = edges.find(
      (e) => e.source === source && e.target === target
    );
    const isHandoff =
      (flowEdge?.data as Record<string, unknown> | undefined)?.edgeType === "handoff" ||
      (flowEdge?.label === "Handoff");
    dslEdges.push({
      source,
      target,
      ...(isHandoff ? { type: "handoff", trigger: "handoff" } : {}),
    });
  }

  edgeGroups.forEach((routes, source) => {
    dslEdges.push({ source, target: Object.fromEntries(routes) });
  });

  return {
    version: "1.0" as const,
    name,
    state: {
      messages: { type: "topic" as const, default: [] },
      context: { type: "last_value" as const, default: "" },
    },
    nodes: dslNodes,
    edges: dslEdges,
    entry: nodes[0]?.id || "",
  };
}
