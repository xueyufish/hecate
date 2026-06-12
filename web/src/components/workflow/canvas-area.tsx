"use client";

import { useCallback, useState } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  applyNodeChanges,
  applyEdgeChanges,
  addEdge,
  getBezierPath,
  type Edge,
  type EdgeProps,
  type BaseEdge,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { nodeTypeComponents } from "./node-types";
import { EdgeTypeSelector } from "./edge-type-selector";

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
}: EdgeProps) {
  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
  });
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
      <text
        x={labelX}
        y={labelY}
        textAnchor="middle"
        dominantBaseline="middle"
        className="text-[10px] fill-purple-600"
      >
        Handoff
      </text>
    </>
  );
}

function ConditionalEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  label,
}: EdgeProps) {
  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
  });
  return (
    <>
      <path
        id={id}
        className="react-flow__edge-path"
        d={edgePath}
        strokeWidth={2}
        stroke="#d97706"
        strokeDasharray="3 6"
        fill="none"
      />
      {typeof label === "string" && label && (
        <text
          x={labelX}
          y={labelY}
          textAnchor="middle"
          dominantBaseline="middle"
          className="text-[10px] fill-amber-700"
        >
          {label}
        </text>
      )}
    </>
  );
}

function FanOutEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
}: EdgeProps) {
  const [edgePath] = getBezierPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
  });
  return (
    <path
      id={id}
      className="react-flow__edge-path"
      d={edgePath}
      strokeWidth={2}
      stroke="#6366f1"
      fill="none"
    />
  );
}

function DynamicHandoffEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
}: EdgeProps) {
  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
  });
  return (
    <>
      <path
        id={id}
        className="react-flow__edge-path"
        d={edgePath}
        strokeWidth={2}
        stroke="#7c3aed"
        strokeDasharray="6 2 2 2"
        fill="none"
      />
      <text
        x={labelX}
        y={labelY - 8}
        textAnchor="middle"
        dominantBaseline="middle"
        className="text-[10px] fill-violet-700"
      >
        ✦ Dynamic
      </text>
    </>
  );
}

const edgeTypes = {
  handoff: HandoffEdge,
  conditional: ConditionalEdge,
  fanout: FanOutEdge,
  dynamic_handoff: DynamicHandoffEdge,
};

export default function CanvasArea({
  nodes,
  edges,
  onNodesChange,
  onEdgesChange,
  onNodeClick,
  onPaneClick,
}: CanvasAreaProps) {
  const [edgeSelector, setEdgeSelector] = useState<{
    x: number;
    y: number;
    params: any;
  } | null>(null);

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

  function handleEdgeTypeSelect(
    type: "default" | "handoff" | "conditional" | "dynamic_handoff",
    label?: string
  ) {
    if (!edgeSelector) return;
    const { params } = edgeSelector;

    const isExistingEdge =
      typeof params.id === "string" && params.id.startsWith("e-");

    const edgeConfigs: Record<string, any> = {
      default: {
        animated: true,
        type: undefined,
        style: undefined,
        label: "",
        data: { edgeType: "default" },
      },
      handoff: {
        animated: false,
        type: "handoff",
        style: { stroke: "#8b5cf6", strokeWidth: 2, strokeDasharray: "5 5" },
        label: "Handoff",
        data: { edgeType: "handoff" },
      },
      conditional: {
        animated: false,
        type: "conditional",
        style: { stroke: "#d97706", strokeWidth: 2, strokeDasharray: "3 6" },
        label: label || "Condition",
        data: { edgeType: "conditional", conditionLabel: label },
      },
      dynamic_handoff: {
        animated: false,
        type: "dynamic_handoff",
        style: { stroke: "#7c3aed", strokeWidth: 2, strokeDasharray: "6 2 2 2" },
        label: "Dynamic Handoff",
        data: { edgeType: "dynamic_handoff" },
      },
    };

    if (isExistingEdge) {
      const updatedEdges = edges.map((e: any) => {
        if (e.id !== params.id) return e;
        return { ...e, ...edgeConfigs[type] };
      });
      onEdgesChange(updatedEdges);
    } else {
      const newEdge = { ...params, ...edgeConfigs[type] };
      onEdgesChange(addEdge(newEdge, edges));
    }
    setEdgeSelector(null);
  }

  const handleConnect = useCallback(
    (params: any) => {
      const isHandoff = params.sourceHandle === "handoff";
      const targetNode = nodes.find((n: any) => n.id === params.target);
      const isFanOut = targetNode?.type === "fan-out";

      if (isHandoff) {
        const newEdge: Edge = {
          ...params,
          animated: false,
          type: "handoff",
          style: { stroke: "#8b5cf6", strokeWidth: 2, strokeDasharray: "5 5" },
          label: "Handoff",
          data: { edgeType: "handoff" },
        };
        onEdgesChange(addEdge(newEdge, edges));
        return;
      }

      if (isFanOut) {
        const newEdge = {
          ...params,
          animated: false,
          type: "fanout",
          style: { stroke: "#6366f1", strokeWidth: 2 },
          data: { edgeType: "fanout" },
        };
        onEdgesChange(addEdge(newEdge, edges));
        return;
      }

      setEdgeSelector({
        x: params.sourceX || 400,
        y: params.sourceY || 300,
        params,
      });
    },
    [edges, nodes, onEdgesChange]
  );

  const handleEdgeClick = useCallback(
    (_event: any, edge: any) => {
      setEdgeSelector({
        x: _event.clientX || 400,
        y: _event.clientY || 300,
        params: edge,
      });
    },
    []
  );

  const typedEdges = edges.map((edge: any) => {
    const edgeType = edge.data?.edgeType;
    if (edgeType === "handoff" || edge.label === "Handoff") {
      return { ...edge, type: "handoff", animated: false };
    }
    if (edgeType === "conditional") {
      return { ...edge, type: "conditional", animated: false };
    }
    if (edgeType === "dynamic_handoff") {
      return { ...edge, type: "dynamic_handoff", animated: false };
    }
    if (edgeType === "fanout") {
      return { ...edge, type: "fanout", animated: false };
    }
    const sourceNode = nodes.find((n: any) => n.id === edge.source);
    if (sourceNode?.type === "fan-out") {
      return {
        ...edge,
        type: "fanout",
        animated: false,
        style: { stroke: "#6366f1", strokeWidth: 2 },
      };
    }
    return edge;
  });

  return (
    <>
      <ReactFlow
        nodes={nodes}
        edges={typedEdges}
        onNodesChange={handleNodesChange}
        onEdgesChange={handleEdgesChange}
        onConnect={handleConnect}
        onEdgeClick={handleEdgeClick}
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

      {edgeSelector && (
        <EdgeTypeSelector
          position={{ x: edgeSelector.x, y: edgeSelector.y }}
          onSelect={handleEdgeTypeSelect}
          onCancel={() => setEdgeSelector(null)}
        />
      )}
    </>
  );
}
