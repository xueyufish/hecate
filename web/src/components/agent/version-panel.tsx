"use client";

// Agent version panel (1.3.20): status badge (未提交/开发中/已发布 vN),
// version history with commit / publish / rollback / diff / drift, mirroring
// the workflow versioning UX precedents in components/workflow.

import { useCallback, useEffect, useRef, useState } from "react";
import { GitBranch, History, RotateCcw, Rocket, AlertTriangle, Upload } from "lucide-react";
import { agentVersionsApi } from "@/lib/api-client";
import type {
  AgentToolContractDiagnostics,
  AgentToolContractDiagnostic,
  AgentVersion,
  AgentVersionDrift,
  AgentVersionStatus,
  VersionDiff,
} from "@/lib/api-types";

interface VersionPanelProps {
  agentId: string;
}

const TOOL_CONTRACT_STATUS_LABELS: Record<AgentToolContractDiagnostic["status"], string> = {
  consistent: "一致",
  changed: "契约变化",
  missing: "工具缺失",
  unverified: "未核验",
};

function shortHash(hash: string | null | undefined): string {
  return hash ? (hash.length > 12 ? `${hash.slice(0, 12)}…` : hash) : "未记录";
}

/**
 * Per-tool contract diagnostics. The verdict is honest by construction:
 * "all verified consistent" requires at least one tool and zero changed,
 * missing or unverified entries — an empty drifted list or a batch of
 * unverified legacy snapshots never reads as everything matching.
 */
function ToolContractDiagnosticsView({ diagnostics }: { diagnostics: AgentToolContractDiagnostics }) {
  const scope = ["name", "description", "parameters"];
  if (diagnostics.format_version !== 1 || !Array.isArray(diagnostics.digest_scope)
    || diagnostics.digest_scope.length !== scope.length
    || !scope.every((field, index) => diagnostics.digest_scope[index] === field)) {
    return <div data-testid="tool-contract-not-verified">诊断格式或摘要范围不受支持：工具契约未核验。</div>;
  }
  const { summary } = diagnostics;
  // A same-name rebuild keeps "consistent" but must still be surfaced, so
  // the problem list includes binding changes, not only non-consistent rows.
  const problems = diagnostics.tools.filter((tool) => tool.status !== "consistent" || tool.binding_changed);
  const allVerified =
    summary.total > 0 && summary.total === diagnostics.tools.length && summary.consistent === summary.total
    && problems.length === 0 && summary.unverified === 0 && summary.changed === 0 && summary.missing === 0;
  return (
    <div className="space-y-2" data-testid="tool-contract-diagnostics">
      <div className="font-semibold">工具契约诊断</div>
      <div className="text-muted-foreground" data-testid="tool-contract-scope">
        摘要范围：{diagnostics.digest_scope.join("、")}（模型可见定义）；解析来源：builtin / workspace
        工具行；未发起外部调用。
      </div>
      <div data-testid="tool-contract-summary">
        共 {summary.total} 个工具：一致 {summary.consistent} · 变化 {summary.changed} · 缺失 {summary.missing} · 未核验{" "}
        {summary.unverified}
      </div>
      {problems.length > 0 && (
        <div className="space-y-1" data-testid="tool-contract-problems">
          {problems.map((tool) => (
            <div key={tool.name} data-testid="tool-contract-problem-row">
              {TOOL_CONTRACT_STATUS_LABELS[tool.status]}：{tool.name}
              {tool.binding_changed
                ? (tool.committed_contract_hash && tool.committed_contract_hash === tool.live_contract_hash
                  ? "（解析来源或绑定已变化，记录的契约哈希一致）"
                  : "（解析来源或绑定已变化）")
                : ""}
              {tool.status === "changed"
                ? `：${shortHash(tool.committed_contract_hash)} → ${shortHash(tool.live_contract_hash)}`
                : ""}
              {tool.detail ? ` — ${tool.detail}` : ""}
            </div>
          ))}
        </div>
      )}
      {summary.total === 0 ? (
        <div data-testid="tool-contract-none">该版本未记录任何工具契约，无法核验。</div>
      ) : allVerified ? (
        <div data-testid="tool-contract-all-verified">已核验工具契约全部一致。</div>
      ) : (
        <div className="text-amber-700" data-testid="tool-contract-not-verified">
          存在契约变化、绑定变化、缺失或未核验的工具：未核验不等于一致。
        </div>
      )}
    </div>
  );
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
  const [drift, setDrift] = useState<{ agentId: string; version: number; report: AgentVersionDrift } | null>(null);
  const [diff, setDiff] = useState<VersionDiff | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Sequence guards: a slow drift or refresh response that belongs to a
  // previous Agent or a previous click must never land on the current view.
  const driftRequestRef = useRef(0);
  const refreshSeqRef = useRef(0);

  const refresh = useCallback(async () => {
    const seq = ++refreshSeqRef.current;
    try {
      const [s, list] = await Promise.all([
        agentVersionsApi.status(agentId),
        agentVersionsApi.list(agentId),
      ]);
      if (seq !== refreshSeqRef.current) return;
      setStatus(s);
      setVersions(list.items);
    } catch {
      if (seq !== refreshSeqRef.current) return;
      setError("Failed to load versions");
    } finally {
      if (seq === refreshSeqRef.current) setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    setLoading(true);
    setVersions([]);
    setStatus(null);
    setDrift(null);
    setDiff(null);
    setError(null);
    void refresh();
    return () => {
      ++driftRequestRef.current;
      ++refreshSeqRef.current;
    };
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
    const requestId = ++driftRequestRef.current;
    try {
      const report = await agentVersionsApi.drift(agentId, version);
      if (requestId !== driftRequestRef.current) return;
      setDrift({ agentId, version, report });
    } catch {
      if (requestId !== driftRequestRef.current) return;
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

      {drift && drift.agentId === agentId && (
        <div className="space-y-2 rounded-md border bg-muted/40 px-3 py-2 text-xs" data-testid="drift-report">
          <div className="font-semibold" data-testid="drift-report-version">
            v{drift.version} 漂移检查
          </div>
          {drift.report.tool_contracts ? (
            <ToolContractDiagnosticsView diagnostics={drift.report.tool_contracts} />
          ) : (
            <div className="text-amber-700" data-testid="tool-contract-absent">
              该后端未返回工具契约诊断：工具契约一律视为未核验，不视为一致。
            </div>
          )}
          <div data-testid="reference-drift">
            {drift.report.drifted.length === 0
              ? "引用快照：无技能/引用漂移记录"
              : `引用漂移：${drift.report.drifted
                  .map((d) => `${d.resource_type} '${d.resource_id}'${d.missing ? " (missing)" : ""}`)
                  .join(", ")}`}
          </div>
          <div className="text-muted-foreground" data-testid="drift-scope-note">
            诊断快照不代表运行时实现、权限或业务行为已经固定；未核验不等于一致。
          </div>
        </div>
      )}

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
