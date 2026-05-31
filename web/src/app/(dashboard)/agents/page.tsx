"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
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
import { Bot, Plus, AlertTriangle, Upload } from "lucide-react";

interface Agent {
  id: string;
  name: string;
  mode: string;
  model_config: { model?: string };
  model_available?: boolean | null;
  created_at: string;
}

export default function AgentsPage() {
  const router = useRouter();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [importing, setImporting] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const loadAgents = () => {
    api
      .get<{ items: Agent[]; total: number }>("/api/agents")
      .then((res) => setAgents(res.items || []))
      .catch(() => setAgents([]))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadAgents();
  }, []);

  const handleImport = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const file = files[0];
    setImporting(true);
    try {
      const text = await file.text();
      const data = JSON.parse(text);
      const result = await api.post<{ id: string }>("/api/agents/import", data);
      router.push(`/agents/${result.id}`);
    } catch (err: unknown) {
      const msg =
        err && typeof err === "object" && "error" in err
          ? (err as { error: { message: string } }).error.message
          : "Import failed";
      alert(msg);
    } finally {
      setImporting(false);
    }
  };

  if (loading) {
    return <div className="text-muted-foreground">加载中...</div>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Agent 管理</h1>
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={() => fileRef.current?.click()} disabled={importing}>
            <Upload className="mr-2 h-4 w-4" />
            {importing ? "导入中..." : "导入 Agent"}
          </Button>
          <input
            ref={fileRef}
            type="file"
            accept=".json"
            className="hidden"
            onChange={(e) => handleImport(e.target.files)}
          />
          <Link href="/agents/new">
            <Button>
              <Plus className="mr-2 h-4 w-4" />
              创建 Agent
            </Button>
          </Link>
        </div>
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
                <TableCell>
                  <span className="flex items-center gap-1.5">
                    {agent.model_config?.model || "-"}
                    {agent.model_available === false && (
                      <span title="模型不可用：Provider 已停用或模型已禁用" className="text-yellow-500">
                        <AlertTriangle className="h-4 w-4" />
                      </span>
                    )}
                  </span>
                </TableCell>
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
