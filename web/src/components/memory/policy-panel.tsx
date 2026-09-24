"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const MEMORY_TOOLS = [
  "memory_search",
  "memory_add",
  "memory_update",
  "memory_forget",
  "memory_replace",
  "memory_insert",
  "memory_rethink",
  "conversation_search",
];

interface PolicyRow {
  id: string;
  scope: "workspace" | "agent";
  agent_id: string;
  enabled: boolean;
  tool_subset: string[] | null;
  sharing_ceiling: string | null;
  params: Record<string, Record<string, unknown>> | null;
}

interface ResolvedView {
  values: Record<string, unknown>;
  sources: Record<string, string>;
}

interface Agent {
  id: string;
  name: string;
}

export function PolicyPanel() {
  const [policies, setPolicies] = useState<PolicyRow[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [selectedAgent, setSelectedAgent] = useState<string>("");
  const [toolSubset, setToolSubset] = useState<string[]>([]);
  const [sharingCeiling, setSharingCeiling] = useState<string>("workspace");
  const [ttlDays, setTtlDays] = useState<string>("");
  const [capacity, setCapacity] = useState<string>("");
  const [resolved, setResolved] = useState<ResolvedView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const load = useCallback(async () => {
    const [policyRes, agentRes] = await Promise.all([
      api.get<{ items: PolicyRow[] }>("/api/memory/policies").catch(() => ({ items: [] })),
      api.get<{ items: Agent[] }>("/api/agents").catch(() => ({ items: [] })),
    ]);
    setPolicies(policyRes.items);
    setAgents(agentRes.items);
    if (agentRes.items?.length && !selectedAgent) {
      setSelectedAgent(agentRes.items[0].id);
    }
    const res = await api
      .get<ResolvedView>(
        `/api/memory/policies/resolved${selectedAgent ? `?agent_id=${selectedAgent}` : ""}`,
      )
      .catch(() => null);
    setResolved(res);
  }, [selectedAgent]);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async (scope: "workspace" | "agent") => {
    setError(null);
    setSaved(false);
    const params: Record<string, Record<string, unknown>> = {};
    if (ttlDays !== "") params.ttl = { l3_episodic_days: Number(ttlDays) };
    if (capacity !== "") params.capacity = { l3: Number(capacity) };
    const body = {
      enabled: true,
      tool_subset: toolSubset.length ? toolSubset : null,
      sharing_ceiling: sharingCeiling || null,
      params: Object.keys(params).length ? params : null,
    };
    try {
      await api.put(
        scope === "workspace"
          ? "/api/memory/policies/workspace"
          : `/api/memory/policies/agents/${selectedAgent}`,
        body,
      );
      setSaved(true);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "save failed");
    }
  };

  const toggleTool = (tool: string) => {
    setToolSubset((prev) => (prev.includes(tool) ? prev.filter((t) => t !== tool) : [...prev, tool]));
  };

  const values = (resolved?.values ?? {}) as Record<string, unknown>;
  const sources = resolved?.sources ?? {};
  const sourceLabel = (field: string) => sources[field] ?? "platform";

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Workspace policy</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label>Memory tools</Label>
            <div className="flex flex-wrap gap-3">
              {MEMORY_TOOLS.map((tool) => (
                <label key={tool} className="flex items-center gap-1 text-sm">
                  <input
                    type="checkbox"
                    data-testid={`tool-${tool}`}
                    checked={toolSubset.includes(tool)}
                    onChange={() => toggleTool(tool)}
                  />
                  {tool}
                </label>
              ))}
            </div>
          </div>
          <div className="grid grid-cols-3 gap-4">
            <div className="space-y-1">
              <Label>Sharing ceiling</Label>
              <select
                data-testid="ceiling-select"
                value={sharingCeiling}
                onChange={(e) => setSharingCeiling(e.target.value)}
                className="w-full rounded-md border bg-background px-2 py-1 text-sm"
              >
                <option value="actor">actor</option>
                <option value="team">team</option>
                <option value="workspace">workspace</option>
              </select>
            </div>
            <div className="space-y-1">
              <Label>L3 episodic TTL (days)</Label>
              <Input
                data-testid="ttl-input"
                type="number"
                min={0}
                value={ttlDays}
                onChange={(e) => setTtlDays(e.target.value)}
                placeholder="platform default"
              />
            </div>
            <div className="space-y-1">
              <Label>L3 capacity</Label>
              <Input
                data-testid="capacity-input"
                type="number"
                min={0}
                value={capacity}
                onChange={(e) => setCapacity(e.target.value)}
                placeholder="platform default"
              />
            </div>
          </div>
          <Button data-testid="save-workspace-policy" onClick={() => save("workspace")}>
            Save workspace policy
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Agent override</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1">
            <Label>Agent</Label>
            <select
              data-testid="policy-agent-select"
              value={selectedAgent}
              onChange={(e) => setSelectedAgent(e.target.value)}
              className="w-full rounded-md border bg-background px-2 py-1 text-sm"
            >
              {agents.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </select>
          </div>
          <Button data-testid="save-agent-policy" onClick={() => save("agent")}>
            Save agent override
          </Button>
        </CardContent>
      </Card>

      {error && (
        <div data-testid="policy-error" className="text-sm text-destructive">
          {error}
        </div>
      )}
      {saved && (
        <div data-testid="policy-saved" className="text-sm text-green-600">
          Policy saved.
        </div>
      )}

      {resolved && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">
              Effective policy{selectedAgent ? " (resolved for selected agent)" : ""}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <table className="w-full text-sm">
              <tbody>
                {Object.entries(values).map(([field, value]) => (
                  <tr key={field} data-testid={`resolved-${field}`} className="border-b">
                    <td className="py-1 pr-4 font-medium">{field}</td>
                    <td className="py-1 pr-4">
                      <code className="text-xs">{JSON.stringify(value)}</code>
                    </td>
                    <td className="py-1 text-xs text-muted-foreground">
                      {sourceLabel(field)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}

      {policies.length > 0 && (
        <div className="text-xs text-muted-foreground">
          {policies.length} policy row(s) configured in this workspace.
        </div>
      )}
    </div>
  );
}
