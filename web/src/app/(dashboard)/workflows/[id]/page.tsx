"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import dynamic from "next/dynamic";
import { api } from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import { dslToReactFlow, reactFlowToDsl } from "@/lib/dsl-bridge";
import type { GraphDSL } from "@/lib/workflow-types";
import { NodePalette } from "@/components/workflow/node-palette";
import { ConfigPanel } from "@/components/workflow/config-panel";
import { ArrowLeft, Save, CheckCircle, Play } from "lucide-react";
import Link from "next/link";

/* eslint-disable @typescript-eslint/no-explicit-any */

// Dynamic import to avoid SSR issues with React Flow
const CanvasArea = dynamic(
  () => import("@/components/workflow/canvas-area"),
  { ssr: false }
);

interface TestRunData {
  run_id: string;
  status: string;
  nodes: { node_id: string; status: string }[];
}

interface WorkflowData {
  id: string;
  name: string;
  description: string;
  graph_dsl: GraphDSL;
}

export default function WorkflowEditorPage() {
  const params = useParams();
  const router = useRouter();
  const workflowId = params.id as string;

  const [workflow, setWorkflow] = useState<WorkflowData | null>(null);
  const [nodes, setNodes] = useState<any[]>([]);
  const [edges, setEdges] = useState<any[]>([]);
  const [saving, setSaving] = useState(false);
  const [testResult, setTestResult] = useState<TestRunData | null>(null);
  const [loading, setLoading] = useState(true);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    api
      .get<WorkflowData>(`/api/workflows/${workflowId}`)
      .then((data) => {
        setWorkflow(data);
        if (data.graph_dsl) {
          const { nodes: rfNodes, edges: rfEdges } = dslToReactFlow(
            data.graph_dsl
          );
          setNodes(rfNodes);
          setEdges(rfEdges);
        }
      })
      .catch(() => router.push("/workflows"))
      .finally(() => setLoading(false));
  }, [workflowId, router]);

  const scheduleSave = useCallback(
    (updatedNodes: any[], updatedEdges: any[]) => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
      saveTimerRef.current = setTimeout(() => {
        if (!workflow) return;
        const dsl = reactFlowToDsl(updatedNodes, updatedEdges, workflow.name);
        api
          .put(`/api/workflows/${workflowId}`, { graph_dsl: dsl })
          .catch(() => {});
      }, 2000);
    },
    [workflow, workflowId]
  );

  const handleNodesChange = useCallback(
    (updatedNodes: any[]) => {
      setNodes(updatedNodes);
      scheduleSave(updatedNodes, edges);
    },
    [edges, scheduleSave]
  );

  const handleEdgesChange = useCallback(
    (updatedEdges: any[]) => {
      setEdges(updatedEdges);
      scheduleSave(nodes, updatedEdges);
    },
    [nodes, scheduleSave]
  );

  async function handleSave() {
    if (!workflow) return;
    setSaving(true);
    const dsl = reactFlowToDsl(nodes, edges, workflow.name);
    try {
      await api.put(`/api/workflows/${workflowId}`, { graph_dsl: dsl });
    } finally {
      setSaving(false);
    }
  }

  async function handleValidate() {
    if (!workflow) return;
    try {
      const result = await api.post<{
        valid: boolean;
        errors: string[];
      }>(`/api/workflows/${workflowId}/validate`, {
        graph_dsl: reactFlowToDsl(nodes, edges, workflow.name),
      });
      if (result.valid) {
        alert("验证通过");
      } else {
        alert("验证失败:\n" + result.errors.join("\n"));
      }
    } catch (err: unknown) {
      const apiErr = err as { error?: { message?: string } };
      alert("验证错误: " + (apiErr.error?.message || "未知错误"));
    }
  }

  async function handleTestRun() {
    if (!workflow) return;
    try {
      const result = await api.post<TestRunData>(
        `/api/workflows/${workflowId}/test-run`,
        {
          input_data: { messages: [{ role: "user", content: "test" }] },
          mock: true,
        }
      );
      setTestResult(result);
    } catch (err: unknown) {
      const apiErr = err as { error?: { message?: string } };
      alert("测试运行错误: " + (apiErr.error?.message || "未知错误"));
    }
  }

  function handleNodeUpdate(nodeId: string, data: Record<string, unknown>) {
    setNodes((prev: any[]) =>
      prev.map((n: any) => (n.id === nodeId ? { ...n, data } : n))
    );
  }

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      const type = e.dataTransfer.getData("application/reactflow");
      if (!type) return;

      const position = { x: e.clientX - 300, y: e.clientY - 100 };
      const newNode = {
        id: `${type}-${Date.now()}`,
        type,
        position,
        data: {
          label: type,
          type,
          config: {},
        },
      };
      setNodes((prev: any[]) => [...prev, newNode]);
    },
    []
  );

  if (loading) {
    return <div className="text-muted-foreground">加载中...</div>;
  }

  if (!workflow) {
    return null;
  }

  return (
    <div className="flex h-[calc(100vh-4rem)] flex-col">
      {/* Toolbar */}
      <div className="flex items-center justify-between border-b px-4 py-2">
        <div className="flex items-center gap-4">
          <Link href="/workflows">
            <Button variant="ghost" size="sm">
              <ArrowLeft className="mr-1 h-4 w-4" />
              返回
            </Button>
          </Link>
          <h1 className="text-lg font-semibold">{workflow.name}</h1>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={handleValidate}>
            <CheckCircle className="mr-1 h-4 w-4" />
            验证
          </Button>
          <Button variant="outline" size="sm" onClick={handleTestRun}>
            <Play className="mr-1 h-4 w-4" />
            测试运行
          </Button>
          <Button size="sm" onClick={handleSave} disabled={saving}>
            <Save className="mr-1 h-4 w-4" />
            {saving ? "保存中..." : "保存"}
          </Button>
        </div>
      </div>

      {/* Canvas + Panels */}
      <div className="flex flex-1">
        <NodePalette />
        <div
          className="flex-1"
          onDragOver={(e) => {
            e.preventDefault();
            e.dataTransfer.dropEffect = "move";
          }}
          onDrop={handleDrop}
        >
          <CanvasArea
            nodes={nodes}
            edges={edges}
            onNodesChange={handleNodesChange}
            onEdgesChange={handleEdgesChange}
          />
        </div>
        <ConfigPanel
          node={null}
          onUpdate={handleNodeUpdate}
          onClose={() => {}}
        />
      </div>

      {/* Test Run Result */}
      {testResult && (
        <div className="border-t bg-muted/30 p-3">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium">
              测试结果:{" "}
              {testResult.status === "completed" ? "✅ 完成" : "❌ 失败"}
            </span>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setTestResult(null)}
            >
              关闭
            </Button>
          </div>
          <div className="mt-1 flex gap-2 text-xs text-muted-foreground">
            {testResult.nodes?.map((n) => (
              <span key={n.node_id}>
                {n.node_id}: {n.status}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
