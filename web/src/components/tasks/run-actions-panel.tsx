"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { OutcomeBadge, RecoveryStateBadge } from "@/components/tasks/state-badge";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import {
  taskApi, taskErrorCode, taskErrorMessage, type RunActionItem,
} from "@/lib/task-types";

type PanelState =
  | { kind: "loading" }
  | { kind: "unsupported" }
  | { kind: "error"; code: string | null; message: string }
  | { kind: "empty" }
  | { kind: "ready"; actions: RunActionItem[] };

export function RunActionsPanel({ runId }: { runId: string }) {
  const [response, setResponse] = useState<{ runId: string; state: PanelState }>({
    runId, state: { kind: "loading" },
  });
  const [reloadTick, setReloadTick] = useState(0);
  const state: PanelState = response.runId === runId ? response.state : { kind: "loading" };

  useEffect(() => {
    // Scope completion to this request lifetime, including A -> B -> A and
    // Strict Mode effect replay. Matching only the Run ID accepts old facts.
    let active = true;
    setResponse({ runId, state: { kind: "loading" } });
    async function fetchActions() {
      try {
        const body = await taskApi.getActions(runId);
        if (!active) return;
        const next: PanelState = !body.supported
          ? { kind: "unsupported" }
          : body.actions.length === 0
            ? { kind: "empty" }
            : { kind: "ready", actions: body.actions };
        setResponse({ runId, state: next });
      } catch (err) {
        if (active) {
          setResponse({ runId, state: {
            kind: "error", code: taskErrorCode(err), message: taskErrorMessage(err),
          } });
        }
      }
    }
    void fetchActions();
    return () => { active = false; };
  }, [runId, reloadTick]);

  return (
    <div className="space-y-2" aria-live="polite">
      <div className="flex justify-end">
        <Button variant="outline" size="sm" disabled={state.kind === "loading"}
          onClick={() => setReloadTick((t) => t + 1)}>
          刷新
        </Button>
      </div>
      {state.kind === "loading" && <p data-testid="actions-loading">动作记录加载中…</p>}
      {state.kind === "unsupported" && (
        <p data-testid="actions-unsupported">不支持动作查询。当前部署的动作台账不提供此查询。</p>
      )}
      {state.kind === "error" && (
        <p role="alert" data-testid="actions-error">
          动作查询失败：{state.code && `[${state.code}] `}{state.message}
        </p>
      )}
      {state.kind === "empty" && <p data-testid="actions-empty">暂无动作</p>}
      {state.kind === "ready" && (
        <div data-testid="actions-ready">
          <p className="mb-2 text-sm text-muted-foreground">
            恢复状态为台账事实；已领取的动作可能已记录结果。结果列为“未记录”时仅表示尚无已记录结果。
          </p>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>动作</TableHead>
                <TableHead>工具</TableHead>
                <TableHead>恢复状态</TableHead>
                <TableHead>已记录结果</TableHead>
                <TableHead>待对账</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {state.actions.map((action) => (
                <TableRow key={action.action_key} data-testid="action-row">
                  <TableCell className="font-mono text-xs">{action.action_key}</TableCell>
                  <TableCell>{action.tool_name ?? "未记录"}</TableCell>
                  <TableCell><RecoveryStateBadge state={action.state} /></TableCell>
                  <TableCell><OutcomeBadge outcome={action.outcome} /></TableCell>
                  <TableCell>
                    {action.pending_reconciliation ? (
                      <span data-testid="pending-flag" className="font-medium text-destructive">待对账</span>
                    ) : <span className="text-muted-foreground">—</span>}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <p className="mt-1 text-xs text-muted-foreground">
            共 {state.actions.length} 条；结果列“未记录”表示该动作尚无已记录结果
            （{state.actions.filter((a) => !a.outcome).length} 条）。
          </p>
        </div>
      )}
    </div>
  );
}
