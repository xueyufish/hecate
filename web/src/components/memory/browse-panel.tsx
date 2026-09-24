"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

interface Agent {
  id: string;
  name: string;
}

interface MemoryRow {
  id: string;
  content: string;
  memory_type?: string;
  tags?: string[];
  importance: number;
  access_count?: number;
  last_confirmed_at?: string | null;
  archived?: boolean;
}

interface RecallRow {
  recall_id: string;
  session_id: string;
  role: string;
  content: string;
  score: number;
  timestamp: string | null;
}

type Layer = "l3" | "l4" | "recall";

function formatDate(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleString() : "—";
}

export function BrowsePanel({ onChanged }: { onChanged: () => void }) {
  const [layer, setLayer] = useState<Layer>("l3");
  const [agents, setAgents] = useState<Agent[]>([]);
  const [agentId, setAgentId] = useState<string>("");
  const [query, setQuery] = useState("");
  const [rows, setRows] = useState<MemoryRow[]>([]);
  const [recallRows, setRecallRows] = useState<RecallRow[]>([]);
  const [showArchived, setShowArchived] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<{ items: Agent[] }>("/api/agents")
      .then((res) => {
        setAgents(res.items || []);
        if (res.items?.length) setAgentId((prev) => prev || res.items[0].id);
      })
      .catch(() => setAgents([]));
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      if (layer === "l3") {
        if (showArchived) {
          const res = await api.get<{ items: MemoryRow[] }>(
            "/api/memory/governance/archived?target_type=user_memory",
          );
          setRows(res.items);
        } else if (query.trim()) {
          const res = await api.get<{ items: Array<{ memory: MemoryRow }> }>(
            `/api/memory/governance/search?q=${encodeURIComponent(query.trim())}`,
          );
          setRows(res.items.map((h) => h.memory));
        } else {
          const res = await api.get<MemoryRow[]>("/api/memory");
          setRows(res);
        }
      } else if (layer === "l4" && agentId) {
        if (showArchived) {
          const res = await api.get<{ items: MemoryRow[] }>(
            "/api/memory/governance/archived?target_type=knowledge_memory",
          );
          setRows(res.items);
        } else {
          const res = await api.get<{ items: MemoryRow[] }>(
            `/api/agents/${agentId}/knowledge`,
          );
          setRows(res.items);
        }
      } else if (layer === "recall" && agentId && query.trim()) {
        const res = await api.post<{ items: RecallRow[] }>(
          "/api/memory/governance/recall/search",
          { query: query.trim(), agent_id: agentId, limit: 20 },
        );
        setRecallRows(res.items);
      } else if (layer === "recall") {
        setRecallRows([]);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "load failed");
    } finally {
      setLoading(false);
    }
  }, [layer, agentId, query, showArchived]);

  useEffect(() => {
    void load();
  }, [load]);

  const archive = async (targetType: "user_memory" | "knowledge_memory", id: string) => {
    await api.post("/api/memory/governance/archive", {
      target_type: targetType,
      memory_id: id,
      agent_id: agentId || undefined,
    });
    await load();
    onChanged();
  };

  const restore = async (targetType: "user_memory" | "knowledge_memory", id: string) => {
    await api.post("/api/memory/governance/restore", {
      target_type: targetType,
      memory_id: id,
      agent_id: agentId || undefined,
    });
    await load();
    onChanged();
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        {(["l3", "l4", "recall"] as Layer[]).map((l) => (
          <button
            key={l}
            type="button"
            data-testid={`layer-${l}`}
            onClick={() => setLayer(l)}
            className={`rounded-md px-3 py-1 text-sm ${
              layer === l ? "bg-secondary text-secondary-foreground" : "hover:bg-muted"
            }`}
          >
            {l === "l3" ? "User facts" : l === "l4" ? "Knowledge" : "Recall"}
          </button>
        ))}
        {(layer === "l4" || layer === "recall") && (
          <select
            data-testid="agent-select"
            value={agentId}
            onChange={(e) => setAgentId(e.target.value)}
            className="rounded-md border bg-background px-2 py-1 text-sm"
          >
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        )}
        {(layer === "l3" || layer === "recall") && (
          <Input
            data-testid="search-input"
            placeholder={layer === "recall" ? "Search conversations…" : "Semantic search…"}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="max-w-sm"
          />
        )}
        {(layer === "l3" || layer === "l4") && (
          <label className="flex items-center gap-1 text-sm">
            <input
              type="checkbox"
              data-testid="show-archived"
              checked={showArchived}
              onChange={(e) => setShowArchived(e.target.checked)}
            />
            Archived only
          </label>
        )}
      </div>

      {error && <div className="text-sm text-destructive">{error}</div>}
      {loading && <div className="text-sm text-muted-foreground">Loading…</div>}

      {layer === "recall" ? (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Role</TableHead>
              <TableHead>Content</TableHead>
              <TableHead>Score</TableHead>
              <TableHead>Time</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {recallRows.map((r) => (
              <TableRow key={r.recall_id}>
                <TableCell>{r.role}</TableCell>
                <TableCell className="max-w-xl truncate">{r.content}</TableCell>
                <TableCell>{r.score.toFixed(3)}</TableCell>
                <TableCell>{formatDate(r.timestamp)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Content</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>Importance</TableHead>
              <TableHead>Confirmed</TableHead>
              <TableHead>Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((r) => (
              <TableRow key={r.id} data-testid={`memory-row-${r.id}`}>
                <TableCell className="max-w-xl truncate">{r.content}</TableCell>
                <TableCell>
                  {layer === "l4"
                    ? (r.tags || []).join(", ") || "—"
                    : r.memory_type || "—"}
                </TableCell>
                <TableCell>{r.importance.toFixed(2)}</TableCell>
                <TableCell>{formatDate(r.last_confirmed_at)}</TableCell>
                <TableCell>
                  {r.archived || showArchived ? (
                    <Button
                      size="sm"
                      variant="outline"
                      data-testid={`restore-${r.id}`}
                      onClick={() =>
                        restore(layer === "l4" ? "knowledge_memory" : "user_memory", r.id)
                      }
                    >
                      Restore
                    </Button>
                  ) : (
                    <Button
                      size="sm"
                      variant="outline"
                      data-testid={`archive-${r.id}`}
                      onClick={() =>
                        archive(layer === "l4" ? "knowledge_memory" : "user_memory", r.id)
                      }
                    >
                      Archive
                    </Button>
                  )}
                  {r.archived && (
                    <Badge variant="secondary" className="ml-2">
                      archived
                    </Badge>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      <p className="text-xs text-muted-foreground">
        Memories are edited by agents through their memory tools — this console
        does not edit content directly.
      </p>
    </div>
  );
}
