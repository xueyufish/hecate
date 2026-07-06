"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Plus, Trash2, Edit, Zap, ChevronDown, ChevronRight, FlaskConical, Loader2 } from "lucide-react";

interface Provider {
  id: string;
  name: string;
  display_name: string;
  status: string;
  is_enabled: boolean;
  model_count: number;
}

interface Model {
  id: string;
  provider_id: string;
  model_id: string;
  display_name: string;
  model_type: string;
  model_metadata?: {
    modalities?: { input?: string[]; output?: string[] };
    capabilities?: Record<string, boolean>;
    limits?: { context?: number; output?: number };
  };
  is_custom: boolean;
  is_enabled: boolean;
}

function getCapabilityBadges(metadata?: Model["model_metadata"]): string[] {
  if (!metadata) return [];
  const badges: string[] = [];
  const caps = metadata.capabilities || {};
  const mods = metadata.modalities || {};
  const limits = metadata.limits || {};
  for (const [k, v] of Object.entries(caps)) {
    if (v) badges.push(k);
  }
  if (mods.input?.includes("image")) badges.push("vision");
  if (mods.input?.includes("audio")) badges.push("audio");
  if (limits.context && limits.context >= 128000) badges.push(`${Math.floor(limits.context / 1000)}K`);
  return [...new Set(badges)];
}

export default function ModelsPage() {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [loading, setLoading] = useState(true);
  const [showDialog, setShowDialog] = useState(false);
  const [showModelDialog, setShowModelDialog] = useState(false);
  const [editingProvider, setEditingProvider] = useState<Provider | null>(null);
  const [targetProviderId, setTargetProviderId] = useState<string>("");
  const [form, setForm] = useState({
    display_name: "",
    api_key: "",
    base_url: "",
  });
  const [modelForm, setModelForm] = useState({
    model_id: "",
    display_name: "",
  });
  const [testing, setTesting] = useState<string | null>(null);
  const [expandedProvider, setExpandedProvider] = useState<string | null>(null);
  const [models, setModels] = useState<Record<string, Model[]>>({});
  const [showTestDialog, setShowTestDialog] = useState(false);
  const [testTarget, setTestTarget] = useState<Model | null>(null);
  const [testPrompt, setTestPrompt] = useState("Hello, respond with one sentence.");
  const [testResult, setTestResult] = useState<{
    content: string;
    model: string;
    usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
  } | null>(null);
  const [testError, setTestError] = useState<string | null>(null);
  const [testingModel, setTestingModel] = useState(false);

  const fetchProviders = async () => {
    try {
      const res = await api.get<{ items: Provider[] }>("/api/model-providers");
      setProviders(res.items || []);
    } catch {
      setProviders([]);
    } finally {
      setLoading(false);
    }
  };

  const fetchModels = async (providerId: string) => {
    try {
      const res = await api.get<{ items: { provider_id: string; models: Model[] }[] }>("/api/models");
      const items = res.items || [];
      const group = items.find((g) => g.provider_id === providerId);
      setModels((prev) => ({ ...prev, [providerId]: group?.models || [] }));
    } catch {
      setModels((prev) => ({ ...prev, [providerId]: [] }));
    }
  };

  useEffect(() => {
    fetchProviders();
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      if (editingProvider) {
        await api.put(`/api/model-providers/${editingProvider.id}`, {
          display_name: form.display_name,
          api_key: form.api_key || undefined,
          base_url: form.base_url || undefined,
        });
      } else {
        await api.post("/api/model-providers", form);
      }
      setShowDialog(false);
      setEditingProvider(null);
      setForm({ display_name: "", api_key: "", base_url: "" });
      fetchProviders();
    } catch {
      alert("Operation failed");
    }
  };

  const handleAddModel = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api.post("/api/models", {
        provider_id: targetProviderId,
        model_id: modelForm.model_id,
        display_name: modelForm.display_name,
      });
      setShowModelDialog(false);
      setModelForm({ model_id: "", display_name: "" });
      fetchProviders();
      fetchModels(targetProviderId);
    } catch {
      alert("Failed to add model");
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm("Are you sure you want to delete this provider?")) return;
    try {
      await api.delete(`/api/model-providers/${id}`);
      fetchProviders();
    } catch {
      alert("Deletion failed");
    }
  };

  const handleTestConnectivity = async (id: string) => {
    setTesting(id);
    try {
      const res = await api.post<{ status: string; error_message?: string }>(
        `/api/model-providers/${id}/test`,
        {}
      );
      if (res.status === "active") {
        alert("Connectivity test passed");
      } else {
        alert(`Connectivity test failed: ${res.error_message || "Unknown error"}`);
      }
      fetchProviders();
    } catch {
      alert("Test failed");
    } finally {
      setTesting(null);
    }
  };

  const toggleExpand = (providerId: string) => {
    if (expandedProvider === providerId) {
      setExpandedProvider(null);
    } else {
      setExpandedProvider(providerId);
      if (!models[providerId]) {
        fetchModels(providerId);
      }
    }
  };

  const statusColor = (status: string) => {
    switch (status) {
      case "active":
        return "bg-green-100 text-green-800";
      case "error":
        return "bg-red-100 text-red-800";
      default:
        return "bg-gray-100 text-gray-800";
    }
  };

  const statusLabel = (status: string) => {
    switch (status) {
      case "active":
        return "Connected";
      case "error":
        return "Connection failed";
      case "pending":
        return "Pending test";
      default:
        return status;
    }
  };

  if (loading) {
    return <div className="text-muted-foreground">Loading...</div>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Model Providers</h1>
        <Button
          onClick={() => {
            setEditingProvider(null);
            setForm({ display_name: "", api_key: "", base_url: "" });
            setShowDialog(true);
          }}
        >
          <Plus className="mr-2 h-4 w-4" />
          Add Provider
        </Button>
      </div>

      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-8" />
                <TableHead>Name</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Models</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {providers.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} className="text-center text-muted-foreground">
                    No providers yet, click &ldquo;Add Provider&rdquo; to get started
                  </TableCell>
                </TableRow>
              ) : (
                providers.map((p) => (
                  <>
                    <TableRow key={p.id}>
                      <TableCell>
                        <button
                          onClick={() => toggleExpand(p.id)}
                          className="text-muted-foreground hover:text-foreground"
                        >
                          {expandedProvider === p.id ? (
                            <ChevronDown className="h-4 w-4" />
                          ) : (
                            <ChevronRight className="h-4 w-4" />
                          )}
                        </button>
                      </TableCell>
                      <TableCell className="font-medium">{p.display_name}</TableCell>
                      <TableCell>
                        <Badge className={statusColor(p.status)}>{statusLabel(p.status)}</Badge>
                      </TableCell>
                      <TableCell>{p.model_count}</TableCell>
                      <TableCell className="text-right space-x-2">
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => handleTestConnectivity(p.id)}
                          disabled={testing === p.id}
                        >
                          <Zap className="mr-1 h-3 w-3" />
                          {testing === p.id ? "Testing" : "Connectivity Test"}
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => {
                            setEditingProvider(p);
                            setForm({
                              display_name: p.display_name,
                              api_key: "",
                              base_url: "",
                            });
                            setShowDialog(true);
                          }}
                        >
                          <Edit className="h-3 w-3" />
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => handleDelete(p.id)}
                        >
                          <Trash2 className="h-3 w-3" />
                        </Button>
                      </TableCell>
                    </TableRow>
                    {expandedProvider === p.id && (
                      <TableRow key={`${p.id}-models`}>
                        <TableCell colSpan={5} className="bg-muted/30 px-8 py-3">
                          <div className="flex items-center justify-between mb-2">
                            <span className="text-sm font-medium text-muted-foreground">
                              Model List
                            </span>
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => {
                                setTargetProviderId(p.id);
                                setModelForm({ model_id: "", display_name: "" });
                                setShowModelDialog(true);
                              }}
                            >
                              <Plus className="mr-1 h-3 w-3" />
                               Add Model
                            </Button>
                          </div>
                          {(models[p.id] || []).length === 0 ? (
                            <p className="text-sm text-muted-foreground py-2">
                               No models yet, click &ldquo;Add Model&rdquo; to configure
                            </p>
                          ) : (
                            <Table>
                                <TableHeader>
                                  <TableRow>
                                    <TableHead>Model Name</TableHead>
                                    <TableHead>Type</TableHead>
                                    <TableHead>Capabilities</TableHead>
                                    <TableHead className="text-right">Actions</TableHead>
                                  </TableRow>
                                </TableHeader>
                              <TableBody>
                                {(models[p.id] || []).map((m) => (
                                  <TableRow key={m.id}>
                                    <TableCell className="font-medium">
                                      {m.display_name}
                                    </TableCell>
                                    <TableCell>{m.model_type}</TableCell>
                                    <TableCell>
                                      <div className="flex flex-wrap gap-1">
                                        {getCapabilityBadges(m.model_metadata).map((badge) => (
                                          <Badge key={badge} variant="outline" className="text-xs">
                                            {badge}
                                          </Badge>
                                        ))}
                                      </div>
                                    </TableCell>
                                    <TableCell className="text-right">
                                      <Button
                                        variant="ghost"
                                        size="sm"
                                        onClick={() => {
                                          setTestTarget(m);
                                          setTestPrompt("Hello, respond with one sentence.");
                                          setTestResult(null);
                                          setTestError(null);
                                          setShowTestDialog(true);
                                        }}
                                      >
                                        <FlaskConical className="mr-1 h-3 w-3" />
                                         Test
                                      </Button>
                                    </TableCell>
                                  </TableRow>
                                ))}
                              </TableBody>
                            </Table>
                          )}
                        </TableCell>
                      </TableRow>
                    )}
                  </>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {showDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <Card className="w-full max-w-md">
            <CardHeader>
              <CardTitle>
                {editingProvider ? "Edit Provider" : "Add Provider"}
              </CardTitle>
            </CardHeader>
            <CardContent>
              <form onSubmit={handleSubmit} className="space-y-4">
                <div className="space-y-2">
                  <Label>Display Name</Label>
                  <Input
                    value={form.display_name}
                    onChange={(e) =>
                      setForm({ ...form, display_name: e.target.value })
                    }
                    placeholder="ZhipuAI, DeepSeek, OpenAI"
                    required
                  />
                </div>
                <div className="space-y-2">
                  <Label>API Key</Label>
                  <Input
                    type="password"
                    value={form.api_key}
                    onChange={(e) =>
                      setForm({ ...form, api_key: e.target.value })
                    }
                    placeholder={editingProvider ? "Leave empty to keep unchanged" : "Enter API Key"}
                    required={!editingProvider}
                  />
                </div>
                <div className="space-y-2">
                  <Label>API Endpoint (optional)</Label>
                  <Input
                    value={form.base_url}
                    onChange={(e) =>
                      setForm({ ...form, base_url: e.target.value })
                    }
                    placeholder="e.g. https://open.bigmodel.cn/api/paas/v4"
                  />
                  <p className="text-xs text-muted-foreground">
                    Leave empty to use provider&apos;s official endpoint
                  </p>
                </div>
                <div className="flex justify-end gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => setShowDialog(false)}
                  >
                    Cancel
                  </Button>
                  <Button type="submit">Confirm</Button>
                </div>
              </form>
            </CardContent>
          </Card>
        </div>
      )}

      {showModelDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <Card className="w-full max-w-md">
            <CardHeader>
              <CardTitle>Add Model</CardTitle>
            </CardHeader>
            <CardContent>
              <form onSubmit={handleAddModel} className="space-y-4">
                <div className="space-y-2">
                  <Label>Model ID</Label>
                  <Input
                    value={modelForm.model_id}
                    onChange={(e) =>
                      setModelForm({ ...modelForm, model_id: e.target.value })
                    }
                    placeholder="glm-4-flash, deepseek-chat, gpt-4o"
                    required
                  />
                  <p className="text-xs text-muted-foreground">
                    Enter model name directly, e.g. glm-4-flash
                  </p>
                </div>
                <div className="space-y-2">
                  <Label>Display Name</Label>
                  <Input
                    value={modelForm.display_name}
                    onChange={(e) =>
                      setModelForm({ ...modelForm, display_name: e.target.value })
                    }
                    placeholder="GLM-4-Flash"
                    required
                  />
                </div>
                <div className="flex justify-end gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => setShowModelDialog(false)}
                  >
                    Cancel
                  </Button>
                  <Button type="submit">Confirm</Button>
                </div>
              </form>
            </CardContent>
          </Card>
        </div>
      )}
      {showTestDialog && testTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <Card className="w-full max-w-lg">
            <CardHeader>
              <CardTitle>Test Model — {testTarget.display_name}</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                <div className="space-y-2">
                  <Label>Prompt</Label>
                  <Input
                    value={testPrompt}
                    onChange={(e) => setTestPrompt(e.target.value)}
                    placeholder="Enter test message"
                  />
                </div>
                {testError && (
                  <div className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
                    {testError}
                  </div>
                )}
                {testResult && (
                  <div className="rounded-md bg-muted p-3 space-y-2">
                    <p className="text-sm font-medium">Response:</p>
                    <p className="text-sm whitespace-pre-wrap">{testResult.content}</p>
                    <div className="flex gap-4 text-xs text-muted-foreground">
                      <span>Model: {testResult.model}</span>
                      <span>Prompt: {testResult.usage.prompt_tokens} tokens</span>
                      <span>Completion: {testResult.usage.completion_tokens} tokens</span>
                    </div>
                  </div>
                )}
                <div className="flex justify-end gap-2">
                  <Button
                    variant="outline"
                    onClick={() => {
                      setShowTestDialog(false);
                      setTestTarget(null);
                      setTestResult(null);
                      setTestError(null);
                    }}
                  >
                     Close
                  </Button>
                  <Button
                    disabled={testingModel || !testPrompt.trim()}
                    onClick={async () => {
                      setTestingModel(true);
                      setTestResult(null);
                      setTestError(null);
                      try {
                        const res = await api.post<{
                          content: string;
                          model: string;
                          usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
                        }>("/api/models/test", {
                          model_id: testTarget.model_id,
                          prompt: testPrompt,
                        });
                        setTestResult(res);
                      } catch (err: unknown) {
                        const msg = err instanceof Error ? err.message : "Test failed";
                        setTestError(msg);
                      } finally {
                        setTestingModel(false);
                      }
                    }}
                  >
                      {testingModel ? (
                      <>
                        <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                        Testing…
                      </>
                    ) : (
                      "Send Test"
                    )}
                  </Button>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
