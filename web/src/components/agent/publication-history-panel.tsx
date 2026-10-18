"use client";

import { Fragment, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import {
  PUBLICATION_LIST_PAGE_SIZE, createdLabel, outcomeLabel, outcomeTone,
  publicationApi, publicationErrorCode, publicationErrorMessage,
  type PublicationItem, type PublicationListResponse,
} from "@/lib/publication-types";

type PanelState =
  | { kind: "loading" }
  | { kind: "error"; code: string | null; message: string }
  | { kind: "ready"; body: PublicationListResponse; offset: number };

// Response identity: only the fetch for the CURRENT agent + offset may render.
// Covers agent switching and page races; the effect-scoped `active` flag below
// covers same-parameter replays (A -> B -> A, refresh during flight).
interface Tag {
  agentId: string;
  offset: number;
}

function sameTag(a: Tag, b: Tag): boolean {
  return a.agentId === b.agentId && a.offset === b.offset;
}

function operatorLabel(operatorUserId: string | null): string {
  return operatorUserId ?? "未记录";
}

function Monospace({ value }: { value: string | null }) {
  if (!value) return <span className="text-muted-foreground">未记录</span>;
  return <span className="break-all font-mono text-xs">{value}</span>;
}

function EvidenceField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div data-testid="evidence-field">
      <span className="text-muted-foreground">{label}：</span>
      {children}
    </div>
  );
}

function signalsOf(report: Record<string, unknown>): Array<Record<string, unknown>> | null {
  if (!Array.isArray(report.signals)) return null;
  return report.signals.filter(
    (s): s is Record<string, unknown> => typeof s === "object" && s !== null
  );
}

function signalVerdict(signal: Record<string, unknown>): string {
  if (signal.enabled === false) return "未启用";
  if (signal.passed === true) return "通过";
  if (signal.passed === false) return "未通过";
  return "未记录";
}

/**
 * Frozen gate-report snapshot. Known fields render structured; unknown or
 * malformed snapshots fall back to the raw JSON — a receipt is a historical
 * fact, shown as stored, never dropped or prettified into another meaning.
 */
function GateReportView({ report }: { report: Record<string, unknown> | null }) {
  if (!report) return <span className="text-muted-foreground">未记录</span>;
  const mode = typeof report.mode === "string" ? report.mode : null;
  const bypassed = typeof report.bypassed_by_force === "boolean" ? report.bypassed_by_force : null;
  const passRate =
    typeof report.deterministic_pass_rate === "number"
      ? report.deterministic_pass_rate
      : null;
  const signals = signalsOf(report);
  if (mode === null && passRate === null && signals === null) {
    return <pre className="max-h-48 overflow-auto rounded bg-muted p-2 text-xs" data-testid="gate-report-raw">{JSON.stringify(report, null, 2)}</pre>;
  }
  return (
    <div className="space-y-1" data-testid="gate-report-structured">
      <EvidenceField label="门禁模式">{mode ?? "未记录"}</EvidenceField>
      <EvidenceField label="force 绕过">{bypassed === null ? "未记录" : bypassed ? "是" : "否"}</EvidenceField>
      {passRate !== null && (
        <EvidenceField label="确定性通过率">{String(passRate)}</EvidenceField>
      )}
      {signals !== null && signals.length > 0 && (
        <div>
          <span className="text-muted-foreground">信号：</span>
          <ul className="ml-4 list-disc">
            {signals.map((signal, index) => (
              <li key={index} data-testid="gate-signal">
                {typeof signal.name === "string" ? signal.name : "未记录"}
                ：{signalVerdict(signal)}
              </li>
            ))}
          </ul>
        </div>
      )}
      <details>
        <summary className="cursor-pointer text-muted-foreground">原始报告快照</summary>
        <pre className="mt-1 max-h-48 overflow-auto rounded bg-muted p-2 text-xs">
          {JSON.stringify(report, null, 2)}
        </pre>
      </details>
    </div>
  );
}

function EvidenceRow({ item }: { item: PublicationItem }) {
  return (
    <TableRow data-testid={`evidence-${item.id}`}>
      <TableCell colSpan={5} className="bg-muted/40">
        <div className="space-y-2 text-sm">
          <EvidenceField label="版本配置哈希">
            <Monospace value={item.agent_config_hash} />
          </EvidenceField>
          <EvidenceField label="candidate 评测 Run">
            <Monospace value={item.candidate_run_id} />
          </EvidenceField>
          <EvidenceField label="candidate 配置哈希">
            <Monospace value={item.candidate_run_hash} />
          </EvidenceField>
          <EvidenceField label="baseline 评测 Run（仅启用回归比较时记录）">
            <Monospace value={item.baseline_run_id} />
          </EvidenceField>
          <EvidenceField label="baseline 配置哈希">
            <Monospace value={item.baseline_run_hash} />
          </EvidenceField>
          <GateConfigView config={item.gate_config} />
          <EvidenceField label="门禁报告摘要">
            <GateReportView report={item.gate_report} />
          </EvidenceField>
          <p className="text-xs text-muted-foreground">
            以上均为该次发布时点冻结的凭据快照，不反映当前评测或门禁配置。
          </p>
        </div>
      </TableCell>
    </TableRow>
  );
}

function GateConfigView({ config }: { config: Record<string, unknown> | null }) {
  return (
    <EvidenceField label="门禁配置（发布时冻结）">
      {config ? (
        <pre className="mt-1 max-h-48 overflow-auto rounded bg-muted p-2 text-xs">
          {JSON.stringify(config, null, 2)}
        </pre>
      ) : <span className="text-muted-foreground">未记录</span>}
    </EvidenceField>
  );
}

/** Keep pagination, query lifetimes and expanded receipts local to one Agent. */
export function PublicationHistoryPanel({ agentId }: { agentId: string }) {
  return <PublicationHistoryContent key={agentId} agentId={agentId} />;
}

function PublicationHistoryContent({ agentId }: { agentId: string }) {
  const [offset, setOffset] = useState(0);
  const [reloadTick, setReloadTick] = useState(0);
  // Retain the last known total so a failed page still offers navigation.
  const [total, setTotal] = useState<number | null>(null);
  const [response, setResponse] = useState<{ tag: Tag; state: PanelState }>({
    tag: { agentId, offset: 0 }, state: { kind: "loading" },
  });
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const current: Tag = { agentId, offset };
  const state: PanelState = sameTag(response.tag, current)
    ? response.state
    : { kind: "loading" };

  useEffect(() => {
    let active = true;
    setResponse({ tag: { agentId, offset }, state: { kind: "loading" } });
    async function fetchPage() {
      try {
        const body = await publicationApi.list(agentId, offset, PUBLICATION_LIST_PAGE_SIZE);
        if (active) {
          setTotal(body.total);
          setResponse({ tag: { agentId, offset }, state: { kind: "ready", body, offset } });
        }
      } catch (err) {
        if (active) setResponse({ tag: { agentId, offset }, state: {
          kind: "error", code: publicationErrorCode(err), message: publicationErrorMessage(err),
        } });
      }
    }
    void fetchPage();
    return () => { active = false; };
  }, [agentId, offset, reloadTick]);

  const totalPages = Math.max(1, Math.ceil((total ?? 0) / PUBLICATION_LIST_PAGE_SIZE));
  const currentPage = Math.floor(offset / PUBLICATION_LIST_PAGE_SIZE) + 1;

  const toggleExpanded = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <div className="space-y-3" data-testid="publication-history-panel">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">发布历史</h2>
        <Button variant="outline" size="sm" data-testid="publications-refresh"
          onClick={() => setReloadTick((t) => t + 1)}>
          刷新
        </Button>
      </div>

      {state.kind === "loading" && (
        <p data-testid="publications-loading">发布历史加载中…</p>
      )}
      {state.kind === "error" && (
        <div data-testid="publications-error">
          <p role="alert">
            发布历史查询失败：{state.code && `[${state.code}] `}{state.message}
          </p>
          <Button variant="outline" size="sm" onClick={() => setReloadTick((t) => t + 1)}>
            重试
          </Button>
        </div>
      )}
      {state.kind === "ready" && (
        <>
          {state.body.items.length === 0 && (
            <div data-testid={offset > 0 ? "publications-empty-page" : "publications-empty"}>
              {offset > 0 ? (
                <p>当前页暂无发布凭据</p>
              ) : (
                <p>
                  暂无发布凭据。发布凭据能力引入前的历史发布可能没有记录，这不代表该
                  Agent 从未发布。
                </p>
              )}
            </div>
          )}
          {state.body.items.length > 0 && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-24" />
                  <TableHead>发布时间</TableHead>
                  <TableHead>版本</TableHead>
                  <TableHead>操作人</TableHead>
                  <TableHead>发布结论</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {state.body.items.map((item) => (
                  <Fragment key={item.id}>
                    <TableRow data-testid="publication-row">
                      <TableCell>
                        <Button variant="ghost" size="sm"
                          aria-expanded={expanded.has(item.id)}
                          onClick={() => toggleExpanded(item.id)}>
                          {expanded.has(item.id) ? "收起证据" : "展开证据"}
                        </Button>
                      </TableCell>
                      <TableCell>{createdLabel(item.created_at)}</TableCell>
                      <TableCell>v{item.version}</TableCell>
                      <TableCell>
                        <span className="font-mono text-xs">
                          {operatorLabel(item.operator_user_id)}
                        </span>
                      </TableCell>
                      <TableCell>
                        <Badge variant={outcomeTone(item.outcome)}
                          data-testid={`outcome-${item.id}`}>
                          {outcomeLabel(item.outcome)}
                        </Badge>
                      </TableCell>
                    </TableRow>
                    {expanded.has(item.id) && <EvidenceRow item={item} />}
                  </Fragment>
                ))}
              </TableBody>
            </Table>
          )}
        </>
      )}
      {total !== null && (
          <div className="flex items-center gap-3" data-testid="publications-pagination">
            <span className="text-sm text-muted-foreground">
              共 {total} 条 · 第 {currentPage} 页 · 共 {totalPages} 页
            </span>
            <Button variant="outline" size="sm"
              disabled={offset <= 0}
              onClick={() => setOffset((o) => Math.max(0, o - PUBLICATION_LIST_PAGE_SIZE))}>
              上一页
            </Button>
            <Button variant="outline" size="sm"
              disabled={offset + PUBLICATION_LIST_PAGE_SIZE >= total}
              onClick={() => setOffset((o) => o + PUBLICATION_LIST_PAGE_SIZE)}>
              下一页
            </Button>
          </div>
      )}
    </div>
  );
}
