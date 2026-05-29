"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

interface Model {
  id: string;
  provider?: string;
  provider_display_name?: string;
}

interface ModelGroup {
  provider_name: string;
  provider_display_name: string;
  models: Model[];
}

export default function NewAgentPage() {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [model, setModel] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [modelGroups, setModelGroups] = useState<ModelGroup[]>([]);
  const [loading, setLoading] = useState(false);
  const router = useRouter();

  useEffect(() => {
    api
      .get<{ data: Model[] }>("/v1/models")
      .then((res) => {
        const models = res.data || [];
        const grouped: Record<string, ModelGroup> = {};
        for (const m of models) {
          const key = m.provider || "unknown";
          if (!grouped[key]) {
            grouped[key] = {
              provider_name: key,
              provider_display_name: m.provider_display_name || key,
              models: [],
            };
          }
          grouped[key].models.push(m);
        }
        setModelGroups(Object.values(grouped));
      })
      .catch(() => setModelGroups([]));
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      const agent = await api.post<{ id: string }>("/api/agents", {
        name,
        mode: "chat",
        model_config: { model },
        persona: systemPrompt || undefined,
      });
      router.push(`/agents/${agent.id}`);
    } catch {
      alert("创建失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <h1 className="text-2xl font-semibold">创建 Agent</h1>
      <Card>
        <CardHeader>
          <CardTitle>基本信息</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="name">名称</Label>
              <Input
                id="name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
                placeholder="给 Agent 起个名字"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="desc">描述</Label>
              <Input
                id="desc"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Agent 的用途描述"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="model">模型选择</Label>
              <select
                id="model"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                required
                className="flex h-10 w-full rounded-md border bg-background px-3 py-2 text-sm"
              >
                <option value="">选择模型</option>
                {modelGroups.length === 0 ? (
                  <option value="" disabled>
                    暂无可用模型，请先配置服务商
                  </option>
                ) : (
                  modelGroups.map((group) => (
                    <optgroup
                      key={group.provider_name}
                      label={group.provider_display_name}
                    >
                      {group.models.map((m) => (
                        <option key={m.id} value={m.id}>
                          {m.id}
                        </option>
                      ))}
                    </optgroup>
                  ))
                )}
              </select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="prompt">系统提示词</Label>
              <Textarea
                id="prompt"
                value={systemPrompt}
                onChange={(e) => setSystemPrompt(e.target.value)}
                rows={4}
                placeholder="你是一个..."
              />
            </div>
            <Button type="submit" disabled={loading} className="w-full">
              {loading ? "创建中..." : "创建"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
