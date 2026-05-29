"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { api } from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { AlertTriangle } from "lucide-react";

interface Agent {
  id: string;
  name: string;
  persona: string | null;
  mode: string;
  model_config: { model?: string };
  model_available?: boolean | null;
}

export default function AgentDetailPage() {
  const params = useParams();
  const router = useRouter();
  const agentId = params.id as string;
  const [agent, setAgent] = useState<Agent | null>(null);
  const [chatLoading, setChatLoading] = useState(false);

  useEffect(() => {
    api.get<Agent>(`/api/agents/${agentId}`).then(setAgent);
  }, [agentId]);

  const startChat = async () => {
    setChatLoading(true);
    try {
      const conv = await api.post<{ id: string }>("/api/conversations", {
        agent_id: agentId,
      });
      router.push(`/chat/${conv.id}`);
    } catch {
      alert("创建对话失败");
    } finally {
      setChatLoading(false);
    }
  };

  if (!agent) {
    return <div className="text-muted-foreground">加载中...</div>;
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">{agent.name}</h1>
        <Button onClick={startChat} disabled={chatLoading || agent.model_available === false}>
          {agent.model_available === false
            ? "模型不可用"
            : chatLoading
              ? "创建中..."
              : "开始对话"}
        </Button>
      </div>

      {agent.model_available === false && (
        <div className="flex items-center gap-2 rounded-md border border-yellow-300 bg-yellow-50 px-4 py-3 text-sm text-yellow-800">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          <span>
            该 Agent 使用的模型「{agent.model_config?.model}」当前不可用，
            可能是 Provider 已停用或模型已禁用。请前往设置检查模型配置。
          </span>
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle>基本信息</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label>名称</Label>
            <Input value={agent.name} readOnly />
          </div>
          <div className="space-y-2">
            <Label>模型</Label>
            <Input value={agent.model_config?.model || ""} readOnly />
          </div>
          <div className="space-y-2">
            <Label>系统提示词</Label>
            <Textarea value={agent.persona || ""} readOnly rows={3} />
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
