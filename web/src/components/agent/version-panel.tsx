"use client";

// Agent version panel (1.3.20): status badge (未提交/开发中/已发布 vN),
// version history with commit / publish / rollback / diff / drift, mirroring
// the workflow versioning UX precedents in components/workflow.

import { useCallback, useEffect, useState } from "react";
import { GitBranch, History, RotateCcw, Rocket, AlertTriangle, Upload } from "lucide-react";
import { agentVersionsApi } from "@/lib/api-client";
import type { AgentVersion, AgentVersionStatus, VersionDiff } from "@/lib/api-types";

interface VersionPanelProps {
  agentId: string;
}

function Badge({ status, published }: { status: AgentVersionStatus | null; published: boolean }) {
  if (!status || status.latest_version === null) {
    return (
      <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-600">未提交</span>
    );
  }
  if (published) {
    return (
      <span className="rounded-full bg-green-100 px-2 py-0.5 text-xs text-green-700">
        已发布 v{status.published_version}
      </span>
    );
  }
  return (
    <span className="rounded-full bg-blue-100 px-2 py-0.5 text-xs text-blue-700">开发中</span>
  );
}

export function VersionPanel({ agentId }: VersionPanelProps) {
  const [status, setStatus] = useState<AgentVersionStatus | null>(null);
  const [versions, setVersions] = useState<AgentVersion[]>([]);
  const [loading, setLoading] = useState(true);
  const [commitOpen, setCommitOpen] = useState(false);
  const [commitName, setCommitName] = useState("");
  const [commitSummary, setCommitSummary] = useState("");
  const [drift, setDrift] = useState<string | null>(null);
  const [diff, setDiff] = useState<VersionDiff | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [s, list] = await Promise.all([
        agentVersionsApi.status(agentId),
        agentVersionsApi.list(agentId),
      ]);
      setStatus(s);
      setVersions(list.items);
    } catch {
      setError("Failed to load versions");
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handleCommit = async () => {
    setError(null);
    try {
      await agentVersionsApi.commit(agentId, {
        name: commitName || undefined,
        change_summary: commitSummary || undefined,
      });
      setCommitOpen(false);
      setCommitName("");
      setCommitSummary("");
      await refresh();
    } catch {
      setError("Commit failed");
    }
  };

  const handlePublish = async (version: number) => {
    setError(null);
    try {
      await agentVersionsApi.publish(agentId, version);
      await refresh();
    } catch (e) {
      const detail = (e as { data?: { error?: { code?: string } } }).data?.error;
      setError(
        detail?.code === "EVALUATION_GATE_BLOCKED"
          ? "Publish blocked by evaluation gate (use force to bypass)"
          : "Publish failed"
      );
    }
  };

  const handleForcePublish = async (version: number) => {
    setError(null);
    try {
      await agentVersionsApi.publish(agentId, version, true);
      await refresh();
    } catch {
      setError("Force publish failed");
    }
  };

  const handleRollback = async (version: number) => {
    setError(null);
    try {
      await agentVersionsApi.rollback(agentId, version);
      await refresh();
    } catch {
      setError("Rollback failed");
    }
  };

  const handleDiff = async () => {
    if (versions.length < 2) return;
    const newest = versions[0].version;
    const oldest = versions[versions.length - 1].version;
    try {
      setDiff(await agentVersionsApi.diff(agentId, oldest, newest));
    } catch {
      setError("Diff failed");
    }
  };

  const handleDrift = async (version: number) => {
    setError(null);
    try {
      const report = await agentVersionsApi.drift(agentId, version);
      setDrift(
        report.drifted.length === 0
          ? `v${version}: all references match the committed content`
          : `v${version} drift: ${report.drifted
              .map((d) => `${d.resource_type} '${d.resource_id}'${d.missing ? " (missing)" : ""}`)
              .join(", ")}`
      );
    } catch {
      setError("Drift check failed");
    }
  };

  const published = (status?.published_version ?? null) !== null;

  return (
    <div className="space-y-3 rounded-md border p-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <History className="h-4 w-4" />
          <h2 className="text-sm font-semibold">版本管理</h2>
          <Badge status={status} published={published} />
          {status?.has_uncommitted_changes && status.latest_version !== null && (
            <span className="text-xs text-amber-600">有未提交变更</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {versions.length >= 2 && (
            <button onClick={handleDiff} className="rounded-md border px-2 py-1 text-xs hover:bg-muted">
              <GitBranch className="mr-1 inline h-3 w-3" />
              Diff oldest→newest
            </button>
          )}
          <button
            onClick={() => setCommitOpen((v) => !v)}
            className="rounded-md bg-primary px-2 py-1 text-xs text-primary-foreground hover:bg-primary/90"
          >
            <Upload className="mr-1 inline h-3 w-3" />
            提交版本
          </button>
        </div>
      </div>

      {commitOpen && (
        <div className="space-y-2 rounded-md border bg-muted/40 p-3">
          <input
            value={commitName}
            onChange={(e) => setCommitName(e.target.value)}
            placeholder="版本名称（可选）"
            className="w-full rounded-md border px-2 py-1 text-sm"
          />
          <input
            value={commitSummary}
            onChange={(e) => setCommitSummary(e.target.value)}
            placeholder="变更说明（可选）"
            className="w-full rounded-md border px-2 py-1 text-sm"
          />
          <button
            onClick={handleCommit}
            className="rounded-md bg-primary px-3 py-1 text-xs text-primary-foreground hover:bg-primary/90"
          >
            确认提交
          </button>
        </div>
      )}

      {drift && <div className="rounded-md border bg-muted/40 px-3 py-2 text-xs">{drift}</div>}

      {diff && (
        <div className="space-y-1 rounded-md border bg-muted/40 p-3 text-xs">
          <div className="font-semibold">
            v{diff.v1} → v{diff.v2} {diff.identical ? "(identical)" : ""}
          </div>
          <div>字段变更: {String(diff.summary.values_changed ?? 0)}</div>
          <div>引用变更: {String(diff.summary.reference_changes ?? 0)}</div>
          {(diff.details.reference_changes ?? []).map((c, i) => (
            <div key={i}>
              {c.change}: {c.resource_type} &quot;{c.resource_id}&quot;
            </div>
          ))}
        </div>
      )}

      {error && (
        <div className="flex items-center gap-2 rounded-md border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-700">
          <AlertTriangle className="h-3 w-3" />
          {error}
        </div>
      )}

      {loading ? (
        <div className="text-muted-foreground text-sm">Loading versions...</div>
      ) : versions.length === 0 ? (
        <div className="text-muted-foreground text-sm">
          尚无版本。提交版本后即可发布到渠道。
        </div>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-muted-foreground">
              <th className="py-1">版本</th>
              <th className="py-1">名称</th>
              <th className="py-1">说明</th>
              <th className="py-1">状态</th>
              <th className="py-1 text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            {versions.map((v) => (
              <tr key={v.id} className="border-t">
                <td className="py-1">v{v.version}</td>
                <td className="py-1">{v.name || "-"}</td>
                <td className="max-w-[12rem] truncate py-1 text-xs text-muted-foreground">
                  {v.change_summary || "-"}
                </td>
                <td className="py-1 text-xs">
                  {v.is_published ? (
                    <span className="text-green-700">已发布</span>
                  ) : (
                    <span className="text-muted-foreground">草稿版本</span>
                  )}
                </td>
                <td className="py-1 text-right">
                  <div className="flex items-center justify-end gap-1">
                    <button
                      onClick={() => handleDrift(v.version)}
                      className="rounded border px-1.5 py-0.5 text-xs hover:bg-muted"
                      title="检查引用漂移"
                    >
                      漂移
                    </button>
                    {!v.is_published && (
                      <>
                        <button
                          onClick={() => handlePublish(v.version)}
                          className="rounded border px-1.5 py-0.5 text-xs hover:bg-muted"
                          title="发布"
                        >
                          <Rocket className="inline h-3 w-3" />
                        </button>
                        <button
                          onClick={() => handleForcePublish(v.version)}
                          className="rounded border px-1.5 py-0.5 text-xs hover:bg-muted"
                          title="强制发布（跳过门禁）"
                        >
                          Force
                        </button>
                        <button
                          onClick={() => handleRollback(v.version)}
                          className="rounded border px-1.5 py-0.5 text-xs hover:bg-muted"
                          title="回滚到此版本（生成新版本）"
                        >
                          <RotateCcw className="inline h-3 w-3" />
                        </button>
                      </>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
