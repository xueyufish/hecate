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
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { nodeTypeComponents } from "./node-types";

/* eslint-disable @typescript-eslint/no-explicit-any */

interface CanvasAreaProps {
  nodes: any[];
  edges: any[];
  onNodesChange: (nodes: any[]) => void;
  onEdgesChange: (edges: any[]) => void;
}

export default function CanvasArea({
  nodes,
  edges,
  onNodesChange,
  onEdgesChange,
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
      onEdgesChange(addEdge({ ...params, animated: true }, edges));
    },
    [edges, onEdgesChange]
  );

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      onNodesChange={handleNodesChange}
      onEdgesChange={handleEdgesChange}
      onConnect={handleConnect}
      nodeTypes={nodeTypeComponents}
      fitView
      style={{ width: "100%", height: "100%" }}
    >
      <Background />
      <Controls />
      <MiniMap />
    </ReactFlow>
  );
}
