"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api-client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Brain, Archive, ScrollText, ShieldCheck } from "lucide-react";
import { BrowsePanel } from "@/components/memory/browse-panel";
import { AuditPanel } from "@/components/memory/audit-panel";
import { PolicyPanel } from "@/components/memory/policy-panel";

export interface LifecycleStats {
  counts: { l3_active: number; l4_active: number; pending_flush_windows: number };
  lifecycle_operations_by_reason: Record<string, number>;
  recent_consolidation_runs: Array<{
    id: string;
    trigger: string;
    status: string;
    adopted: number;
    created_at: string;
  }>;
}

type Section = "browse" | "audit" | "policy";

const SECTIONS: Array<{ key: Section; label: string; icon: typeof Brain }> = [
  { key: "browse", label: "Memories", icon: Brain },
  { key: "audit", label: "Audit", icon: ScrollText },
  { key: "policy", label: "Policy", icon: ShieldCheck },
];

export default function MemoryCenterPage() {
  const [section, setSection] = useState<Section>("browse");
  const [stats, setStats] = useState<LifecycleStats | null>(null);

  const loadStats = useCallback(() => {
    api
      .get<LifecycleStats>("/api/memory/governance/stats")
      .then(setStats)
      .catch(() => setStats(null));
  }, []);

  useEffect(() => {
    loadStats();
  }, [loadStats]);

  return (
    <div className="space-y-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold">Memory Center</h1>
        <p className="text-sm text-muted-foreground">
          Browse, audit and govern agent memories. Content edits go through
          the agent tool path — this console browses, archives and restores.
        </p>
      </div>

      {stats && (
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium">L3 facts</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{stats.counts.l3_active}</div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium">L4 knowledge</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{stats.counts.l4_active}</div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium">Pending flush windows</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{stats.counts.pending_flush_windows}</div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium flex items-center gap-1">
                <Archive className="h-4 w-4" /> Lifecycle ops
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-0.5 text-xs text-muted-foreground">
                {Object.entries(stats.lifecycle_operations_by_reason).length === 0 && (
                  <div>none yet</div>
                )}
                {Object.entries(stats.lifecycle_operations_by_reason).map(([reason, count]) => (
                  <div key={reason}>
                    {reason}: {count}
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      <div className="flex gap-2 border-b pb-2">
        {SECTIONS.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            type="button"
            data-testid={`section-${key}`}
            onClick={() => setSection(key)}
            className={`flex items-center gap-2 rounded-md px-3 py-1.5 text-sm ${
              section === key ? "bg-primary text-primary-foreground" : "hover:bg-muted"
            }`}
          >
            <Icon className="h-4 w-4" />
            {label}
          </button>
        ))}
      </div>

      {section === "browse" && <BrowsePanel onChanged={loadStats} />}
      {section === "audit" && <AuditPanel />}
      {section === "policy" && <PolicyPanel />}
    </div>
  );
}
