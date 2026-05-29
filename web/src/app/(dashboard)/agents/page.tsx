"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Bot, Plus } from "lucide-react";

interface Agent {
  id: string;
  name: string;
  mode: string;
  model_config: { model?: string };
  created_at: string;
}

export default function AgentsPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get<{ items: Agent[]; total: number }>("/api/agents")
      .then((res) => setAgents(res.items || []))
      .catch(() => setAgents([]))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return <div className="text-muted-foreground">加载中...</div>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Agent 管理</h1>
        <Link href="/agents/new">
          <Button>
            <Plus className="mr-2 h-4 w-4" />
            创建 Agent
          </Button>
        </Link>
      </div>

      {agents.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-lg border border-dashed py-16">
          <Bot className="mb-4 h-12 w-12 text-muted-foreground" />
          <p className="mb-4 text-muted-foreground">
            还没有 Agent，点击创建第一个
          </p>
          <Link href="/agents/new">
            <Button>创建 Agent</Button>
          </Link>
        </div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>名称</TableHead>
              <TableHead>模型</TableHead>
              <TableHead>创建时间</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {agents.map((agent) => (
              <TableRow key={agent.id}>
                <TableCell>
                  <Link
                    href={`/agents/${agent.id}`}
                    className="font-medium hover:underline"
                  >
                    {agent.name}
                  </Link>
                </TableCell>
                <TableCell>{agent.model_config?.model || "-"}</TableCell>
                <TableCell>
                  {new Date(agent.created_at).toLocaleDateString("zh-CN")}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
