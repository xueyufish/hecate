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
  is_custom: boolean;
  is_enabled: boolean;
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
      alert("操作失败");
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
      alert("添加模型失败");
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm("确定删除该服务商？")) return;
    try {
      await api.delete(`/api/model-providers/${id}`);
      fetchProviders();
    } catch {
      alert("删除失败");
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
        alert("连通测试成功");
      } else {
        alert(`连通测试失败：${res.error_message || "未知错误"}`);
      }
      fetchProviders();
    } catch {
      alert("测试失败");
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
        return "已连通";
      case "error":
        return "连接失败";
      case "pending":
        return "待测试";
      default:
        return status;
    }
  };

  if (loading) {
    return <div className="text-muted-foreground">加载中...</div>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">模型服务商</h1>
        <Button
          onClick={() => {
            setEditingProvider(null);
            setForm({ display_name: "", api_key: "", base_url: "" });
            setShowDialog(true);
          }}
        >
          <Plus className="mr-2 h-4 w-4" />
          添加服务商
        </Button>
      </div>

      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-8" />
                <TableHead>名称</TableHead>
                <TableHead>状态</TableHead>
                <TableHead>模型数</TableHead>
                <TableHead className="text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {providers.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} className="text-center text-muted-foreground">
                    暂无服务商，点击&ldquo;添加服务商&rdquo;开始配置
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
                          {testing === p.id ? "测试中" : "连通测试"}
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
                              模型列表
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
                              添加模型
                            </Button>
                          </div>
                          {(models[p.id] || []).length === 0 ? (
                            <p className="text-sm text-muted-foreground py-2">
                              暂无模型，点击&ldquo;添加模型&rdquo;配置
                            </p>
                          ) : (
                            <Table>
                              <TableHeader>
                                <TableRow>
                                  <TableHead>模型名称</TableHead>
                                  <TableHead>类型</TableHead>
                                  <TableHead className="text-right">操作</TableHead>
                                </TableRow>
                              </TableHeader>
                              <TableBody>
                                {(models[p.id] || []).map((m) => (
                                  <TableRow key={m.id}>
                                    <TableCell className="font-medium">
                                      {m.display_name}
                                    </TableCell>
                                    <TableCell>{m.model_type}</TableCell>
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
                                        测试
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
                {editingProvider ? "编辑服务商" : "添加服务商"}
              </CardTitle>
            </CardHeader>
            <CardContent>
              <form onSubmit={handleSubmit} className="space-y-4">
                <div className="space-y-2">
                  <Label>显示名</Label>
                  <Input
                    value={form.display_name}
                    onChange={(e) =>
                      setForm({ ...form, display_name: e.target.value })
                    }
                    placeholder="智谱AI, DeepSeek, OpenAI"
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
                    placeholder={editingProvider ? "留空则不修改" : "输入 API Key"}
                    required={!editingProvider}
                  />
                </div>
                <div className="space-y-2">
                  <Label>API 端点（可选）</Label>
                  <Input
                    value={form.base_url}
                    onChange={(e) =>
                      setForm({ ...form, base_url: e.target.value })
                    }
                    placeholder="如 https://open.bigmodel.cn/api/paas/v4"
                  />
                  <p className="text-xs text-muted-foreground">
                    不填则使用服务商官方端点
                  </p>
                </div>
                <div className="flex justify-end gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => setShowDialog(false)}
                  >
                    取消
                  </Button>
                  <Button type="submit">确定</Button>
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
              <CardTitle>添加模型</CardTitle>
            </CardHeader>
            <CardContent>
              <form onSubmit={handleAddModel} className="space-y-4">
                <div className="space-y-2">
                  <Label>模型 ID</Label>
                  <Input
                    value={modelForm.model_id}
                    onChange={(e) =>
                      setModelForm({ ...modelForm, model_id: e.target.value })
                    }
                    placeholder="glm-4-flash, deepseek-chat, gpt-4o"
                    required
                  />
                  <p className="text-xs text-muted-foreground">
                    直接填模型名称即可，如 glm-4-flash
                  </p>
                </div>
                <div className="space-y-2">
                  <Label>显示名</Label>
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
                    取消
                  </Button>
                  <Button type="submit">确定</Button>
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
              <CardTitle>测试模型 — {testTarget.display_name}</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                <div className="space-y-2">
                  <Label>Prompt</Label>
                  <Input
                    value={testPrompt}
                    onChange={(e) => setTestPrompt(e.target.value)}
                    placeholder="输入测试消息"
                  />
                </div>
                {testError && (
                  <div className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
                    {testError}
                  </div>
                )}
                {testResult && (
                  <div className="rounded-md bg-muted p-3 space-y-2">
                    <p className="text-sm font-medium">回复：</p>
                    <p className="text-sm whitespace-pre-wrap">{testResult.content}</p>
                    <div className="flex gap-4 text-xs text-muted-foreground">
                      <span>模型: {testResult.model}</span>
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
                    关闭
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
                        const msg = err instanceof Error ? err.message : "测试失败";
                        setTestError(msg);
                      } finally {
                        setTestingModel(false);
                      }
                    }}
                  >
                    {testingModel ? (
                      <>
                        <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                        测试中…
                      </>
                    ) : (
                      "发送测试"
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
