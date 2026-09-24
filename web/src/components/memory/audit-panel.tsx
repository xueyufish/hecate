"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api-client";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

interface AuditRow {
  id: string;
  agent_id: string;
  tool_name: string;
  target_type: string;
  target_id: string;
  reason: string | null;
  before_summary: string | null;
  after_summary: string | null;
  created_at: string;
}

interface RunRow {
  id: string;
  kind: string;
  trigger: string;
  status: string;
  adopted_count: number;
  rejected_count: number;
  error: string | null;
  created_at: string;
}

export function AuditPanel() {
  const [auditRows, setAuditRows] = useState<AuditRow[]>([]);
  const [runRows, setRunRows] = useState<RunRow[]>([]);
  const [source, setSource] = useState("");
  const [runStatus, setRunStatus] = useState("");
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const auditQuery = source ? `?tool_name=${encodeURIComponent(source)}` : "";
      const audit = await api.get<{ items: AuditRow[] }>(
        `/api/memory/governance/audit${auditQuery}`,
      );
      setAuditRows(audit.items);
      const runsQuery = runStatus ? `?run_status=${encodeURIComponent(runStatus)}` : "";
      const runs = await api.get<{ items: RunRow[] }>(
        `/api/memory/governance/runs${runsQuery}`,
      );
      setRunRows(runs.items);
    } catch {
      setAuditRows([]);
      setRunRows([]);
    } finally {
      setLoading(false);
    }
  }, [source, runStatus]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-8">
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <h2 className="text-lg font-medium">Edit log</h2>
          <select
            data-testid="audit-source-filter"
            value={source}
            onChange={(e) => setSource(e.target.value)}
            className="rounded-md border bg-background px-2 py-1 text-sm"
          >
            <option value="">All sources</option>
            <option value="consolidation">consolidation</option>
            <option value="lifecycle">lifecycle</option>
            <option value="memory_update">memory_update</option>
            <option value="memory_add">memory_add</option>
            <option value="memory_forget">memory_forget</option>
            <option value="policy_upsert">policy_upsert</option>
          </select>
          {loading && <span className="text-xs text-muted-foreground">Loading…</span>}
        </div>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Time</TableHead>
              <TableHead>Source</TableHead>
              <TableHead>Target</TableHead>
              <TableHead>Reason</TableHead>
              <TableHead>Detail</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {auditRows.map((r) => (
              <TableRow key={r.id} data-testid={`audit-row-${r.id}`}>
                <TableCell>{new Date(r.created_at).toLocaleString()}</TableCell>
                <TableCell>{r.tool_name}</TableCell>
                <TableCell className="max-w-[16rem] truncate">
                  {r.target_type}/{r.target_id.slice(0, 8)}
                </TableCell>
                <TableCell>{r.reason ?? "—"}</TableCell>
                <TableCell className="max-w-md truncate">
                  {r.after_summary ?? r.before_summary ?? "—"}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <h2 className="text-lg font-medium">Consolidation runs</h2>
          <select
            data-testid="runs-status-filter"
            value={runStatus}
            onChange={(e) => setRunStatus(e.target.value)}
            className="rounded-md border bg-background px-2 py-1 text-sm"
          >
            <option value="">All statuses</option>
            <option value="success">success</option>
            <option value="partial">partial</option>
            <option value="failed">failed</option>
          </select>
        </div>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Time</TableHead>
              <TableHead>Trigger</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Adopted</TableHead>
              <TableHead>Error</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {runRows.map((r) => (
              <TableRow key={r.id} data-testid={`run-row-${r.id}`}>
                <TableCell>{new Date(r.created_at).toLocaleString()}</TableCell>
                <TableCell>{r.trigger}</TableCell>
                <TableCell>{r.status}</TableCell>
                <TableCell>{r.adopted_count}</TableCell>
                <TableCell className="max-w-md truncate">{r.error ?? "—"}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
