"use client";

import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { OutcomeBadge, RecoveryStateBadge } from "@/components/tasks/state-badge";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import {
  taskApi, taskErrorCode, taskErrorMessage, type ActionEvidenceReceipt,
  type DigestVerification, type RunActionItem,
} from "@/lib/task-types";

type PanelState =
  | { kind: "loading" }
  | { kind: "unsupported" }
  | { kind: "error"; code: string | null; message: string }
  | { kind: "empty" }
  | { kind: "ready"; taskId: string; actions: RunActionItem[] };

type ReceiptState =
  | { kind: "loading" }
  | { kind: "unsupported" }
  | { kind: "not-found" }
  | { kind: "error"; code: string | null; message: string }
  | { kind: "ready"; receipt: ActionEvidenceReceipt };

/** The stable identity of one action: the ledger key, never a row index. */
type Selection = { runId: string; actionKey: string };

const VERIFICATION_LABELS: Record<string, string> = {
  verified: "本次核验一致",
  mismatch: "摘要不匹配",
  unverifiable: "无法复核",
  unsupported: "不支持复核",
};

const RESULT_STATE_LABELS: Record<string, string> = {
  recorded: "已记录结果",
  missing: "未记录结果",
  unknown: "结果未知",
};

export function RunActionsPanel({ runId }: { runId: string }) {
  const [response, setResponse] = useState<{ runId: string; state: PanelState }>({
    runId, state: { kind: "loading" },
  });
  const [reloadTick, setReloadTick] = useState(0);
  const state: PanelState = response.runId === runId ? response.state : { kind: "loading" };

  // The selected action and its receipt are scoped to the Run they were
  // requested for, so a Run switch can never show the previous Run's
  // evidence.
  const [selection, setSelection] = useState<Selection | null>(null);
  const [receipt, setReceipt] = useState<{ selection: Selection; state: ReceiptState } | null>(null);
  const [receiptTick, setReceiptTick] = useState(0);
  const [copiedSelection, setCopiedSelection] = useState<Selection | null>(null);
  const lastRun = useRef<string | null>(null);

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
            : { kind: "ready", taskId: body.task_id, actions: body.actions };
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

  // A different Run invalidates the selection: the previous action may not
  // even exist in the new Run.
  useEffect(() => {
    if (lastRun.current === runId) return;
    const params = new URLSearchParams(window.location.search);
    const key = lastRun.current === null && params.get("run_id") === runId ? params.get("action_key") : null;
    lastRun.current = runId;
    setSelection(key ? { runId, actionKey: key } : null);
    setReceipt(null);
    setCopiedSelection(null);
  }, [runId]);

  useEffect(() => {
    if (!selection || selection.runId !== runId) return;
    const current = selection;
    let active = true;
    setReceipt({ selection: current, state: { kind: "loading" } });
    async function fetchReceipt() {
      try {
        const body = await taskApi.getActionEvidence(current.runId, current.actionKey);
        if (!active) return;
        if (body.run_id !== current.runId || (body.receipt && (
          body.receipt.run_id !== current.runId || body.receipt.action_key !== current.actionKey ||
          body.receipt.task_id !== body.task_id
        ))) {
          throw new Error("证据响应与所选动作不一致");
        }
        const next: ReceiptState = !body.supported
          ? { kind: "unsupported" }
          : body.receipt
            ? { kind: "ready", receipt: body.receipt }
            : { kind: "not-found" };
        setReceipt({ selection: current, state: next });
      } catch (err) {
        if (!active) return;
        const next: ReceiptState = taskErrorCode(err) === "NOT_FOUND"
          ? { kind: "not-found" }
          : { kind: "error", code: taskErrorCode(err), message: taskErrorMessage(err) };
        setReceipt({ selection: current, state: next });
      }
    }
    void fetchReceipt();
    return () => { active = false; };
  }, [selection, receiptTick, runId]);

  const selectedKey = selection?.runId === runId ? selection.actionKey : null;
  const receiptState: ReceiptState | null =
    receipt && receipt.selection === selection && receipt.selection.runId === runId
      ? receipt.state
      : null;
  const taskId = state.kind === "ready" ? state.taskId
    : receiptState?.kind === "ready" ? receiptState.receipt.task_id : null;
  const referenceUrl = taskId && selectedKey ? `${window.location.origin}/ops-center/tasks/${encodeURIComponent(taskId)}?${
    new URLSearchParams({ run_id: runId, action_key: selectedKey }).toString()
  }` : null;

  function select(actionKey: string) {
    setCopiedSelection(null);
    setSelection((current) =>
      current && current.runId === runId && current.actionKey === actionKey
        ? current
        : { runId, actionKey },
    );
  }

  async function copyReference(value: string, current: Selection) {
    try {
      await navigator.clipboard.writeText(value);
      setCopiedSelection(current);
    } catch {
      // A clipboard that refuses is not a failure of the receipt: the
      // reference stays visible and selectable for manual copying.
      setCopiedSelection(null);
    }
  }

  return (
    <div className="space-y-2" aria-live="polite">
      <div className="flex justify-end">
        <Button variant="outline" size="sm" disabled={state.kind === "loading"}
          onClick={() => { setReloadTick((t) => t + 1); setReceiptTick((t) => t + 1); }}>
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
                <TableHead>证据</TableHead>
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
                  <TableCell>
                    <Button
                      variant="outline"
                      size="sm"
                      data-testid="action-evidence-open"
                      onClick={() => select(action.action_key)}
                    >
                      查看证据
                    </Button>
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
      {selectedKey && (
        <div className="space-y-2 rounded border p-3" data-testid="evidence-panel">
          <div className="flex items-center justify-between gap-2">
            <p className="text-sm font-medium">动作证据回执</p>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                data-testid="evidence-relocate"
                onClick={() => setReceiptTick((t) => t + 1)}
              >
                刷新证据
              </Button>
              <Button
                variant="outline"
                size="sm"
                data-testid="evidence-copy-ref"
                disabled={!referenceUrl || !selection}
                onClick={() => { if (referenceUrl && selection) void copyReference(referenceUrl, selection); }}
              >
                复制稳定引用
              </Button>
            </div>
          </div>
          <p className="font-mono text-xs text-muted-foreground" data-testid="evidence-ref">
            {referenceUrl ? <a href={referenceUrl} className="underline">{referenceUrl}</a> : selectedKey}
          </p>
          {copiedSelection && copiedSelection === selection && (
            <p className="text-xs text-muted-foreground" data-testid="evidence-copied">已复制稳定引用</p>
          )}
          {receiptState?.kind === "loading" && (
            <p className="text-sm" data-testid="evidence-loading">证据回执加载中…</p>
          )}
          {receiptState?.kind === "unsupported" && (
            <p className="text-sm" data-testid="evidence-unsupported">不支持证据查询。</p>
          )}
          {receiptState?.kind === "not-found" && (
            <p className="text-sm" data-testid="evidence-not-found">未找到该动作的证据记录。</p>
          )}
          {receiptState?.kind === "error" && (
            <p role="alert" className="text-sm" data-testid="evidence-error">
              证据查询失败：{receiptState.code && `[${receiptState.code}] `}{receiptState.message}
            </p>
          )}
          {receiptState?.kind === "ready" && <EvidenceReceiptView receipt={receiptState.receipt} />}
        </div>
      )}
    </div>
  );
}

function EvidenceReceiptView({ receipt }: { receipt: ActionEvidenceReceipt }) {
  return (
    <div className="space-y-2" data-testid="evidence-ready">
      <p className="text-xs text-muted-foreground">
        回执为已记录事实的投影；摘要一致只证明保存内容相同，不代表业务目标已通过。
      </p>
      <dl className="grid grid-cols-1 gap-1 text-xs sm:grid-cols-2">
        <ReceiptFact label="Task" value={receipt.task_id} testId="evidence-task-id" />
        <ReceiptFact label="Run" value={receipt.run_id} testId="evidence-run-id" />
        <ReceiptFact label="动作" value={receipt.action_key} testId="evidence-action-key" />
        <ReceiptFact label="执行标识" value={receipt.execution_id} testId="evidence-execution-id" />
        <ReceiptFact label="工具调用标识" value={receipt.tool_call_id} testId="evidence-tool-call-id" />
        <ReceiptFact label="工具" value={receipt.tool_name} testId="evidence-tool" />
        <ReceiptFact label="副作用分类" value={receipt.side_effect_class} testId="evidence-side-effect" />
        <ReceiptFact label="恢复状态" value={receipt.recovery_state} testId="evidence-recovery-state" />
        <ReceiptFact label="已记录结果" value={receipt.outcome} testId="evidence-outcome" />
        <ReceiptFact label="待对账" value={receipt.pending_reconciliation ? "是" : "否"} testId="evidence-reconciliation" />
        <ReceiptFact
          label="结果状态"
          value={RESULT_STATE_LABELS[receipt.result_state] ?? receipt.result_state}
          testId="evidence-result-state"
        />
        <ReceiptFact label="结果记录时间" value={receipt.outcome_recorded_at} testId="evidence-recorded-at" />
        <ReceiptFact label="参数摘要" value={receipt.arguments_digest} testId="evidence-arguments-digest" />
        <ReceiptFact label="结果摘要" value={receipt.result_digest} testId="evidence-result-digest" />
        <ReceiptFact
          label="决策引用"
          value={
            receipt.decision_ref.status === "linked"
              ? `${receipt.decision_ref.decision_step_id ?? ""}（${receipt.decision_ref.content_digest ?? ""}）`
              : "未记录"
          }
          testId="evidence-decision-ref"
        />
        <ReceiptFact label="读取时间" value={receipt.read_at} testId="evidence-read-at" />
      </dl>
      <div className="space-y-1 text-xs">
        <VerificationFact
          title="参数摘要核验"
          verification={receipt.arguments_verification}
          testId="evidence-arguments-verification"
        />
        <VerificationFact
          title="结果摘要核验"
          verification={receipt.result_verification}
          testId="evidence-result-verification"
        />
      </div>
    </div>
  );
}

function ReceiptFact({
  label, value, testId,
}: { label: string; value: string | null; testId: string }) {
  return (
    <div className="flex gap-2">
      <dt className="shrink-0 text-muted-foreground">{label}</dt>
      <dd className="font-mono break-all" data-testid={testId}>{value ?? "未记录"}</dd>
    </div>
  );
}

function VerificationFact({
  title, verification, testId,
}: { title: string; verification: DigestVerification; testId: string }) {
  const mismatch = verification.status === "mismatch";
  return (
    <div className="rounded border p-2" data-testid={testId}>
      <p className="flex items-center gap-2">
        <span className="font-medium">{title}</span>
        <span className={mismatch ? "font-medium text-destructive" : "text-muted-foreground"}>
          {VERIFICATION_LABELS[verification.status] ?? verification.status}
        </span>
      </p>
      <p className="text-muted-foreground">算法：{verification.algorithm}</p>
      <p className="text-muted-foreground">校验对象：{verification.subject}</p>
      <p className="font-mono break-all">已记录：{verification.recorded_digest ?? "未记录"}</p>
      <p className="font-mono break-all">本次计算：{verification.computed_digest ?? "未计算"}</p>
    </div>
  );
}
