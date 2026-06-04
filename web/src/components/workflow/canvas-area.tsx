"use client";

import { useCallback } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  applyNodeChanges,
  applyEdgeChanges,
  addEdge,
  type Edge,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { nodeTypeComponents } from "./node-types";

/* eslint-disable @typescript-eslint/no-explicit-any */

interface CanvasAreaProps {
  nodes: any[];
  edges: any[];
  onNodesChange: (nodes: any[]) => void;
  onEdgesChange: (edges: any[]) => void;
  onNodeClick?: (nodeId: string) => void;
  onPaneClick?: () => void;
}

function HandoffEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
}: {
  id: string;
  sourceX: number;
  sourceY: number;
  targetX: number;
  targetY: number;
}) {
  const edgePath = `M ${sourceX},${sourceY} L ${targetX},${targetY}`;
  return (
    <>
      <path
        id={id}
        className="react-flow__edge-path"
        d={edgePath}
        strokeWidth={2}
        stroke="#8b5cf6"
        strokeDasharray="5 5"
        fill="none"
      />
    </>
  );
}

const edgeTypes = {
  handoff: HandoffEdge,
};

export default function CanvasArea({
  nodes,
  edges,
  onNodesChange,
  onEdgesChange,
  onNodeClick,
  onPaneClick,
}: CanvasAreaProps) {
  const handleNodesChange = useCallback(
    (changes: any) => {
      onNodesChange(applyNodeChanges(changes, nodes));
    },
    [nodes, onNodesChange]
  );

  const handleEdgesChange = useCallback(
    (changes: any) => {
      onEdgesChange(applyEdgeChanges(changes, edges));
    },
    [edges, onEdgesChange]
  );

  const handleConnect = useCallback(
    (params: any) => {
      const isHandoff = params.sourceHandle === "handoff";
      const newEdge: Edge = {
        ...params,
        animated: !isHandoff,
        ...(isHandoff
          ? {
              type: "handoff",
              style: { stroke: "#8b5cf6", strokeWidth: 2, strokeDasharray: "5 5" },
              label: "Handoff",
              data: { edgeType: "handoff" },
            }
          : {}),
      };
      onEdgesChange(addEdge(newEdge, edges));
    },
    [edges, onEdgesChange]
  );

  const typedEdges = edges.map((edge: any) => {
    if (edge.data?.edgeType === "handoff" || edge.label === "Handoff") {
      return { ...edge, type: "handoff", animated: false };
    }
    return edge;
  });

  return (
    <ReactFlow
      nodes={nodes}
      edges={typedEdges}
      onNodesChange={handleNodesChange}
      onEdgesChange={handleEdgesChange}
      onConnect={handleConnect}
      onNodeClick={(_event: any, node: any) => onNodeClick?.(node.id)}
      onPaneClick={() => onPaneClick?.()}
      nodeTypes={nodeTypeComponents}
      edgeTypes={edgeTypes}
      fitView
      style={{ width: "100%", height: "100%" }}
    >
      <Background />
      <Controls />
      <MiniMap />
    </ReactFlow>
  );
}
