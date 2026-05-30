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
  conversation: "对话",
  "tool-call": "工具调用",
  condition: "条件",
  agent: "Agent",
  "knowledge-retrieval": "知识检索",
  "variable-set": "变量设置",
};

/** Convert Graph DSL to React Flow nodes and edges */
export function dslToReactFlow(dsl: ParsedDsl): {
  nodes: FlowNode[];
  edges: Edge[];
} {
  const nodeIds = Object.keys(dsl.nodes);
  const nodes: FlowNode[] = nodeIds.map((id, index) => {
    const nodeDef = dsl.nodes[id];
    return {
      id,
      type: nodeDef.type,
      position: {
        x: 250 * (index % 4),
        y: 150 * Math.floor(index / 4),
      },
      data: {
        label:
          nodeDef.config.system_prompt ||
          NODE_TYPE_LABELS[nodeDef.type] ||
          id,
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
      return {
        id: `e-${index}`,
        source,
        target,
        animated: !isHandoff,
        style: isHandoff ? { stroke: "#8b5cf6", strokeWidth: 2, strokeDasharray: "5 5" } : undefined,
        label: isHandoff ? "移交" : undefined,
        data: { edgeType: isHandoff ? "handoff" : "default" },
      };
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
    if (edge.label && typeof edge.label === "string" && edge.label !== "移交") {
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
      (flowEdge?.label === "移交");
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
