"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import dynamic from "next/dynamic";
import { api } from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { dslToReactFlow, reactFlowToDsl } from "@/lib/dsl-bridge";
import type { GraphDSL } from "@/lib/workflow-types";
import { NodePalette } from "@/components/workflow/node-palette";
import { AgentPalette } from "@/components/workflow/agent-palette";
import { TemplatePicker } from "@/components/workflow/template-picker";
import { PatternSelector } from "@/components/workflow/pattern-selector";
import { PatternConfigDialog } from "@/components/workflow/pattern-config-dialog";
import { ConfigPanel } from "@/components/workflow/config-panel";
import { ArrowLeft, Save, CheckCircle, Play, LayoutTemplate, History, X, ChevronDown, ChevronUp, Network } from "lucide-react";
import Link from "next/link";

/* eslint-disable @typescript-eslint/no-explicit-any */

const CanvasArea = dynamic(
  () => import("@/components/workflow/canvas-area"),
  { ssr: false }
);

interface NodeResult {
  node_id: string;
  node_type: string;
  status: string;
  output?: unknown;
  error_message?: string;
  duration_ms?: number;
}

interface TestRunData {
  run_id: string;
  status: string;
  nodes: NodeResult[];
  total_duration_ms?: number;
  error?: string;
  timestamp?: number;
}

interface WorkflowData {
  id: string;
  name: string;
  description: string;
  graph_dsl: GraphDSL;
}

const MAX_HISTORY = 10;

const LAYOUT_KEY = (id: string) => `hecate-layout-${id}`;

function saveLayout(workflowId: string, nodes: any[]) {
  const layout: Record<string, { x: number; y: number }> = {};
  for (const node of nodes) {
    layout[node.id] = { x: node.position.x, y: node.position.y };
  }
  try {
    localStorage.setItem(LAYOUT_KEY(workflowId), JSON.stringify(layout));
  } catch {
    // localStorage full or unavailable — ignore
  }
}

function loadLayout(workflowId: string): Record<string, { x: number; y: number }> | null {
  try {
    const raw = localStorage.getItem(LAYOUT_KEY(workflowId));
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
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
  const [showTemplatePicker, setShowTemplatePicker] = useState(false);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [showInputForm, setShowInputForm] = useState(false);
  const [inputMessages, setInputMessages] = useState('[{"role": "user", "content": "test"}]');
  const [inputError, setInputError] = useState("");
  const [selectedNode, setSelectedNode] = useState<NodeResult | null>(null);
  const [showLogs, setShowLogs] = useState(false);
  const [runHistory, setRunHistory] = useState<TestRunData[]>([]);
  const [showHistory, setShowHistory] = useState(false);
  const [running, setRunning] = useState(false);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [isCustomizing, setIsCustomizing] = useState(false);
  const [customizingFrom, setCustomizingFrom] = useState<string | null>(null);
  const [saveAsName, setSaveAsName] = useState("");
  const [showSaveAsDialog, setShowSaveAsDialog] = useState(false);
  const [patternSelectorOpen, setPatternSelectorOpen] = useState(false);
  const [selectedPattern, setSelectedPattern] = useState<import("@/lib/workflow-types").PatternDefinition | null>(null);
  const [configDialogOpen, setConfigDialogOpen] = useState(false);

  useEffect(() => {
    api
      .get<WorkflowData>(`/api/workflows/${workflowId}`)
      .then((data) => {
        setWorkflow(data);
        if (data.graph_dsl) {
          const { nodes: rfNodes, edges: rfEdges } = dslToReactFlow(data.graph_dsl);
          const savedLayout = loadLayout(workflowId);
          const mergedNodes = savedLayout
            ? rfNodes.map((n) => ({
                ...n,
                position: savedLayout[n.id] || n.position,
              }))
            : rfNodes;
          setNodes(mergedNodes);
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
        api.put(`/api/workflows/${workflowId}`, { graph_dsl: dsl }).catch(() => {});
        saveLayout(workflowId, updatedNodes);
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
      const result = await api.post<{ valid: boolean; errors: string[] }>(
        `/api/workflows/${workflowId}/validate`,
        { graph_dsl: reactFlowToDsl(nodes, edges, workflow.name) }
      );
      if (result.valid) {
        alert("Validation passed");
      } else {
        alert("Validation failed:\n" + result.errors.join("\n"));
      }
    } catch (err: unknown) {
      const apiErr = err as { error?: { message?: string } };
      alert("Validation error: " + (apiErr.error?.message || "Unknown error"));
    }
  }

  async function handleTestRun() {
    if (!workflow) return;

    let parsedMessages;
    try {
      parsedMessages = JSON.parse(inputMessages);
      if (!Array.isArray(parsedMessages)) {
        setInputError("Messages must be a JSON array");
        return;
      }
      setInputError("");
    } catch {
      setInputError("Invalid JSON");
      return;
    }

    setRunning(true);
    setSelectedNode(null);
    try {
      const result = await api.post<TestRunData>(
        `/api/workflows/${workflowId}/test-run`,
        { input_data: { messages: parsedMessages }, mock: true }
      );
      const withTimestamp = { ...result, timestamp: Date.now() };
      setTestResult(withTimestamp);
      setRunHistory((prev) => [withTimestamp, ...prev].slice(0, MAX_HISTORY));
    } catch (err: unknown) {
      const apiErr = err as { error?: { message?: string } };
      alert("Test run error: " + (apiErr.error?.message || "Unknown error"));
    } finally {
      setRunning(false);
    }
  }

  function handleNodeClick(nodeId: string) {
    if (!testResult) return;
    const nodeResult = testResult.nodes.find((n) => n.node_id === nodeId);
    if (nodeResult) setSelectedNode(nodeResult);
  }

  function clearResults() {
    setTestResult(null);
    setSelectedNode(null);
  }

  function loadHistoryEntry(entry: TestRunData) {
    setTestResult(entry);
    setSelectedNode(null);
    setShowHistory(false);
  }

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();

      const agentData = e.dataTransfer.getData("application/reactflow-agent");
      if (agentData) {
        try {
          const agent = JSON.parse(agentData);
          const position = { x: e.clientX - 300, y: e.clientY - 100 };
          const newNode = {
            id: `agent-${agent.id}-${Date.now()}`,
            type: "agent",
            position,
            data: { label: agent.name, type: "agent", config: { agent_id: agent.id, invocation_mode: "standalone" } },
          };
          setNodes((prev: any[]) => [...prev, newNode]);
        } catch {
          // malformed agent data
        }
        return;
      }

      const type = e.dataTransfer.getData("application/reactflow");
      if (!type) return;

      const position = { x: e.clientX - 300, y: e.clientY - 100 };
      const newNode = {
        id: `${type}-${Date.now()}`,
        type,
        position,
        data: { label: type, type, config: {} },
      };
      setNodes((prev: any[]) => [...prev, newNode]);
    },
    []
  );

  function handleTemplateSelect(dsl: Record<string, unknown>) {
    const graphDsl = dsl as unknown as GraphDSL;
    const { nodes: rfNodes, edges: rfEdges } = dslToReactFlow(graphDsl);
    setNodes(rfNodes);
    setEdges(rfEdges);
    setIsCustomizing(true);
    setCustomizingFrom(graphDsl.name || "Template");
    setShowTemplatePicker(false);
  }

  async function handleSaveAsWorkflow() {
    const name = saveAsName.trim();
    if (!name) return;
    setSaving(true);
    try {
      const dsl = reactFlowToDsl(nodes, edges, name);
      const result = await api.post<{ id: string }>("/api/workflows", {
        name,
        description: `Customized from ${customizingFrom}`,
        graph_dsl: dsl,
      });
      setIsCustomizing(false);
      setCustomizingFrom(null);
      setSaveAsName("");
      setShowSaveAsDialog(false);
      router.push(`/workflows/${result.id}`);
    } finally {
      setSaving(false);
    }
  }

  function handlePatternSelect(pattern: import("@/lib/workflow-types").PatternDefinition) {
    setSelectedPattern(pattern);
    setPatternSelectorOpen(false);
    setConfigDialogOpen(true);
  }

  async function handlePatternGenerate(
    patternId: string,
    config: Record<string, unknown>,
  ) {
    try {
      const graphDsl = await api.post<GraphDSL>(
        `/api/collaboration-patterns/${patternId}/generate`,
        { config },
      );
      const { nodes: rfNodes, edges: rfEdges } = dslToReactFlow(graphDsl);
      setNodes(rfNodes);
      setEdges(rfEdges);
      setIsCustomizing(true);
      setCustomizingFrom(patternId);
      setConfigDialogOpen(false);
      setSelectedPattern(null);
    } catch (err: unknown) {
      const apiErr = err as { error?: { message?: string } };
      alert("Pattern generation error: " + (apiErr.error?.message || "Unknown error"));
    }
  }

  function getNodeStatusColor(status: string) {
    if (status === "completed") return "bg-green-500";
    if (status === "error" || status === "failed") return "bg-red-500";
    if (status === "running") return "bg-yellow-500";
    return "bg-gray-400";
  }

  function truncate(str: string, max: number) {
    return str.length > max ? str.slice(0, max) + "..." : str;
  }

  if (loading) {
    return <div className="text-muted-foreground">Loading...</div>;
  }

  if (!workflow) {
    return null;
  }

  return (
    <div className="flex h-[calc(100vh-4rem)] flex-col">
      <div className="flex items-center justify-between border-b px-4 py-2">
        <div className="flex items-center gap-4">
          <Link href="/workflows">
            <Button variant="ghost" size="sm">
              <ArrowLeft className="mr-1 h-4 w-4" />
              Back
            </Button>
          </Link>
          <h1 className="text-lg font-semibold">{workflow.name}</h1>
        </div>
        <div className="flex items-center gap-2">
          {isCustomizing && (
            <span className="flex items-center gap-1 rounded-md bg-violet-50 px-2 py-1 text-xs font-medium text-violet-700">
              <LayoutTemplate className="h-3 w-3" />
              Customizing: {customizingFrom}
            </span>
          )}
          <Button variant="outline" size="sm" onClick={() => setPatternSelectorOpen(true)}>
            <Network className="mr-1 h-4 w-4" />
            Patterns
          </Button>
          <Button variant="outline" size="sm" onClick={() => setShowTemplatePicker(true)}>
            <LayoutTemplate className="mr-1 h-4 w-4" />
            Templates
          </Button>
          <Button variant="outline" size="sm" onClick={handleValidate}>
            <CheckCircle className="mr-1 h-4 w-4" />
            Validate
          </Button>
          <Button variant="outline" size="sm" onClick={() => setShowInputForm(!showInputForm)}>
            Input
          </Button>
          <Button variant="outline" size="sm" onClick={() => setShowHistory(!showHistory)}>
            <History className="mr-1 h-4 w-4" />
            History ({runHistory.length})
          </Button>
          <Button size="sm" onClick={handleTestRun} disabled={running}>
            <Play className="mr-1 h-4 w-4" />
            {running ? "Running..." : "Test Run"}
          </Button>
          <Button size="sm" onClick={handleSave} disabled={saving}>
            <Save className="mr-1 h-4 w-4" />
            {saving ? "Saving..." : "Save"}
          </Button>
          {isCustomizing && (
            <Button
              variant="default"
              size="sm"
              onClick={() => setShowSaveAsDialog(true)}
            >
              Save as Workflow
            </Button>
          )}
        </div>
      </div>

      <div className="flex flex-1 overflow-hidden">
        <div className="flex h-full w-[200px] flex-col border-r bg-muted/30">
          <NodePalette />
          <div className="border-t px-2 py-2">
            <AgentPalette />
          </div>
        </div>

        <div className="flex flex-1 flex-col">
          <div
            className="flex-1"
            onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = "move"; }}
            onDrop={handleDrop}
          >
            <CanvasArea
              nodes={nodes}
              edges={edges}
              onNodesChange={handleNodesChange}
              onEdgesChange={handleEdgesChange}
              onNodeClick={(nodeId) => setSelectedNodeId(nodeId)}
              onPaneClick={() => setSelectedNodeId(null)}
            />
          </div>

          {testResult && (
            <div className="border-t bg-muted/30 p-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">
                    Test Result: {testResult.status === "completed" ? "✅ Completed" : "❌ Failed"}
                  </span>
                  {testResult.total_duration_ms && (
                    <span className="text-xs text-muted-foreground">
                      {testResult.total_duration_ms}ms
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-1">
                  <Button variant="ghost" size="sm" onClick={() => setShowLogs(!showLogs)}>
                    {showLogs ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
                    Logs
                  </Button>
                  <Button variant="ghost" size="sm" onClick={clearResults}>
                    <X className="h-4 w-4" />
                  </Button>
                </div>
              </div>

              <div className="mt-2 flex flex-wrap gap-2">
                {testResult.nodes.map((n) => (
                  <button
                    key={n.node_id}
                    onClick={() => handleNodeClick(n.node_id)}
                    className={`flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs transition-colors ${
                      selectedNode?.node_id === n.node_id ? "border-primary bg-primary/10" : "hover:bg-muted"
                    }`}
                  >
                    <span className={`h-2 w-2 rounded-full ${getNodeStatusColor(n.status)}`} />
                    <span className="font-medium">{n.node_id}</span>
                    <span className="text-muted-foreground">{n.status}</span>
                    {n.duration_ms && <span className="text-muted-foreground">({n.duration_ms}ms)</span>}
                  </button>
                ))}
              </div>

              {showLogs && (
                <div className="mt-2 max-h-32 overflow-y-auto rounded border bg-background p-2 text-xs font-mono">
                  {testResult.nodes.map((n, i) => (
                    <div key={n.node_id} className="text-muted-foreground">
                      [{i + 1}] {n.node_id} — {n.status}
                      {n.duration_ms && ` (${n.duration_ms}ms)`}
                      {n.error_message && ` — ERROR: ${n.error_message}`}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        <div className="flex h-full w-[300px] flex-col border-l bg-muted/30">
          {showInputForm && (
            <div className="border-b p-3 space-y-2">
              <div className="flex items-center justify-between">
                <Label className="text-xs font-medium">Input Data</Label>
                <Button variant="ghost" size="sm" onClick={() => setShowInputForm(false)}>
                  <X className="h-3 w-3" />
                </Button>
              </div>
              <div className="space-y-1">
                <Label className="text-xs text-muted-foreground">Messages (JSON array)</Label>
                <Textarea
                  value={inputMessages}
                  onChange={(e) => { setInputMessages(e.target.value); setInputError(""); }}
                  rows={4}
                  className="font-mono text-xs"
                />
                {inputError && <p className="text-xs text-red-500">{inputError}</p>}
              </div>
            </div>
          )}

          {selectedNodeId ? (
            <ConfigPanel
              node={nodes.find((n: any) => n.id === selectedNodeId) || null}
              onUpdate={(nodeId: string, data: Record<string, unknown>) => {
                const updatedNodes = nodes.map((n: any) =>
                  n.id === nodeId ? { ...n, data } : n
                );
                setNodes(updatedNodes);
                scheduleSave(updatedNodes, edges);
              }}
              onClose={() => setSelectedNodeId(null)}
              graphStateChannels={
                workflow?.graph_dsl?.state
                  ? Object.keys(workflow.graph_dsl.state)
                  : []
              }
              allNodes={nodes}
              edges={edges}
            />
          ) : selectedNode ? (
            <div className="flex-1 overflow-y-auto p-3 space-y-3">
              <div className="flex items-center justify-between">
                <Label className="text-xs font-medium">Node Details</Label>
                <Button variant="ghost" size="sm" onClick={() => setSelectedNode(null)}>
                  <X className="h-3 w-3" />
                </Button>
              </div>

              <div className="space-y-2 text-xs">
                <div>
                  <span className="text-muted-foreground">Node ID:</span>{" "}
                  <span className="font-medium">{selectedNode.node_id}</span>
                </div>
                <div>
                  <span className="text-muted-foreground">Type:</span>{" "}
                  <span className="font-medium">{selectedNode.node_type}</span>
                </div>
                <div>
                  <span className="text-muted-foreground">Status:</span>{" "}
                  <span className={`inline-flex items-center gap-1`}>
                    <span className={`h-2 w-2 rounded-full ${getNodeStatusColor(selectedNode.status)}`} />
                    {selectedNode.status}
                  </span>
                </div>
                {selectedNode.duration_ms && (
                  <div>
                    <span className="text-muted-foreground">Duration:</span>{" "}
                    <span className="font-medium">{selectedNode.duration_ms}ms</span>
                  </div>
                )}
                {selectedNode.output !== undefined && selectedNode.output !== null && (
                  <div>
                    <span className="text-muted-foreground">Output:</span>
                    <pre className="mt-1 max-h-40 overflow-y-auto whitespace-pre-wrap rounded border bg-background p-2 text-xs">
                      {truncate(
                        typeof selectedNode.output === "string"
                          ? selectedNode.output
                          : JSON.stringify(selectedNode.output, null, 2),
                        1000
                      )}
                    </pre>
                  </div>
                )}
                {selectedNode.error_message && (
                  <div>
                    <span className="text-muted-foreground">Error:</span>
                    <p className="mt-1 rounded border border-red-200 bg-red-50 p-2 text-xs text-red-600">
                      {selectedNode.error_message}
                    </p>
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div className="flex flex-1 items-center justify-center p-3">
              <p className="text-xs text-muted-foreground text-center">
                Click a node to configure it,<br />or click &quot;Input&quot; to configure test data
              </p>
            </div>
          )}
        </div>

        {showHistory && (
          <div className="absolute right-0 top-12 z-50 w-64 rounded-md border bg-background p-2 shadow-md">
            <div className="flex items-center justify-between mb-2">
              <Label className="text-xs font-medium">Run History</Label>
              <Button variant="ghost" size="sm" onClick={() => setShowHistory(false)}>
                <X className="h-3 w-3" />
              </Button>
            </div>
            {runHistory.length === 0 ? (
              <p className="text-xs text-muted-foreground">No history</p>
            ) : (
              <div className="max-h-60 overflow-y-auto space-y-1">
                {runHistory.map((entry, i) => (
                  <button
                    key={entry.run_id + i}
                    onClick={() => loadHistoryEntry(entry)}
                    className="flex w-full items-center justify-between rounded px-2 py-1.5 text-xs hover:bg-muted"
                  >
                    <div className="flex items-center gap-2">
                      <span className={`h-2 w-2 rounded-full ${getNodeStatusColor(entry.status)}`} />
                      <span>{entry.status === "completed" ? "Completed" : "Failed"}</span>
                    </div>
                    <span className="text-muted-foreground">
                      {entry.timestamp ? new Date(entry.timestamp).toLocaleTimeString() : ""}
                      {entry.total_duration_ms ? ` · ${entry.total_duration_ms}ms` : ""}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {showSaveAsDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="w-[360px] rounded-lg border bg-background p-4 shadow-lg">
            <h3 className="mb-3 text-sm font-semibold">Save as New Workflow</h3>
            <input
              type="text"
              className="w-full rounded-md border px-3 py-1.5 text-sm"
              value={saveAsName}
              onChange={(e) => setSaveAsName(e.target.value)}
              placeholder="Workflow name..."
              autoFocus
              onKeyDown={(e) => {
                if (e.key === "Enter") handleSaveAsWorkflow();
                if (e.key === "Escape") setShowSaveAsDialog(false);
              }}
            />
            <div className="mt-3 flex justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setShowSaveAsDialog(false)}
              >
                Cancel
              </Button>
              <Button
                size="sm"
                onClick={handleSaveAsWorkflow}
                disabled={saving || !saveAsName.trim()}
              >
                {saving ? "Saving..." : "Create"}
              </Button>
            </div>
          </div>
        </div>
      )}

      {showTemplatePicker && (
        <TemplatePicker
          onSelect={handleTemplateSelect}
          onClose={() => setShowTemplatePicker(false)}
        />
      )}

      <PatternSelector
        open={patternSelectorOpen}
        onSelect={handlePatternSelect}
        onClose={() => setPatternSelectorOpen(false)}
      />

      <PatternConfigDialog
        open={configDialogOpen}
        pattern={selectedPattern}
        onGenerate={handlePatternGenerate}
        onClose={() => {
          setConfigDialogOpen(false);
          setSelectedPattern(null);
        }}
      />
    </div>
  );
}
