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
import { Plus, Trash2, Edit, Zap } from "lucide-react";

interface Provider {
  id: string;
  name: string;
  display_name: string;
  status: string;
  is_enabled: boolean;
  model_count: number;
}

export default function ModelsPage() {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [loading, setLoading] = useState(true);
  const [showDialog, setShowDialog] = useState(false);
  const [editingProvider, setEditingProvider] = useState<Provider | null>(null);
  const [form, setForm] = useState({
    name: "",
    display_name: "",
    api_key: "",
    base_url: "",
  });
  const [testing, setTesting] = useState<string | null>(null);

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
      setForm({ name: "", display_name: "", api_key: "", base_url: "" });
      fetchProviders();
    } catch {
      alert("操作失败");
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

  const handleTest = async (id: string) => {
    setTesting(id);
    try {
      const res = await api.post<{ status: string }>(
        `/api/model-providers/${id}/test`,
        {}
      );
      alert(res.status === "active" ? "连通测试成功" : "连通测试失败");
      fetchProviders();
    } catch {
      alert("测试失败");
    } finally {
      setTesting(null);
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
            setForm({ name: "", display_name: "", api_key: "", base_url: "" });
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
                <TableHead>名称</TableHead>
                <TableHead>状态</TableHead>
                <TableHead>模型数</TableHead>
                <TableHead className="text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {providers.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={4} className="text-center text-muted-foreground">
                    暂无服务商，点击&ldquo;添加服务商&rdquo;开始配置
                  </TableCell>
                </TableRow>
              ) : (
                providers.map((p) => (
                  <TableRow key={p.id}>
                    <TableCell className="font-medium">{p.display_name}</TableCell>
                    <TableCell>
                      <Badge className={statusColor(p.status)}>{p.status}</Badge>
                    </TableCell>
                    <TableCell>{p.model_count}</TableCell>
                    <TableCell className="text-right space-x-2">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => handleTest(p.id)}
                        disabled={testing === p.id}
                      >
                        <Zap className="mr-1 h-3 w-3" />
                        {testing === p.id ? "测试中" : "测试"}
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => {
                          setEditingProvider(p);
                          setForm({
                            name: p.name,
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
                  <Label>标识名</Label>
                  <Input
                    value={form.name}
                    onChange={(e) => setForm({ ...form, name: e.target.value })}
                    placeholder="openai, zhipu, deepseek"
                    disabled={!!editingProvider}
                    required
                  />
                </div>
                <div className="space-y-2">
                  <Label>显示名</Label>
                  <Input
                    value={form.display_name}
                    onChange={(e) =>
                      setForm({ ...form, display_name: e.target.value })
                    }
                    placeholder="OpenAI, 智谱, DeepSeek"
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
                  <Label>Base URL（可选）</Label>
                  <Input
                    value={form.base_url}
                    onChange={(e) =>
                      setForm({ ...form, base_url: e.target.value })
                    }
                    placeholder="自定义端点"
                  />
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
    </div>
  );
}
