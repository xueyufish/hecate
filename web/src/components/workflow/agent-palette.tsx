"use client";

import { useEffect, useState } from "react";
import { Users } from "lucide-react";
import { api } from "@/lib/api-client";

interface AgentItem {
  id: string;
  name: string;
  mode: string;
  persona: string | null;
}

export function AgentPalette() {
  const [agents, setAgents] = useState<AgentItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get<{ items: AgentItem[]; total: number }>("/api/agents")
      .then((data) => setAgents(data.items || []))
      .catch(() => setAgents([]))
      .finally(() => setLoading(false));
  }, []);

  function onDragStart(e: React.DragEvent, agent: AgentItem) {
    e.dataTransfer.setData(
      "application/reactflow-agent",
      JSON.stringify({ id: agent.id, name: agent.name })
    );
    e.dataTransfer.effectAllowed = "move";
  }

  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs font-semibold text-muted-foreground px-1 py-1">
        已有 Agent
      </span>
      {loading && (
        <span className="text-xs text-muted-foreground px-1">加载中...</span>
      )}
      {!loading && agents.length === 0 && (
        <span className="text-xs text-muted-foreground px-1">暂无 Agent</span>
      )}
      {agents.map((agent) => (
        <div
          key={agent.id}
          draggable
          onDragStart={(e) => onDragStart(e, agent)}
          className="flex cursor-grab items-center gap-2 rounded-md border border-green-200 bg-green-50 px-3 py-1.5 text-sm hover:bg-green-100 active:cursor-grabbing"
        >
          <Users className="h-3.5 w-3.5 text-green-600" />
          <span className="truncate text-xs">{agent.name}</span>
        </div>
      ))}
    </div>
  );
}
