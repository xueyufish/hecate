"use client";

// Human approval decisions for a waiting_approval task. Only the server
// decides who may act (workspace ADMIN, never the task's initiator — the
// identity comes from the AuthContext, never the body); the panel renders
// the whitelisted wait summary and freezes one decision operation: retries
// after unknown network results reuse the same operation id and body, so
// the server replays one decision. Receipts state facts — an applied
// resume says the platform accepted the recorded decision as a wake, not
// that the protected action ran or the task succeeded. A consumed wait, a
// terminal task, or an unsupported/unknown wait shape renders read-only
// with its reason.

import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { commandStateLabel } from "@/lib/task-command-types";
import {
  APPROVAL_DECISION_REASON_LIMIT,
  taskApprovalApi,
  type ApprovalView,
  type DecisionSubmitResponse,
} from "@/lib/task-approval-types";
import { lifecycleLabel, taskErrorCode, taskErrorMessage, type TaskDetail } from "@/lib/task-types";

const TERMINAL_LIFECYCLE_STATES = new Set(["succeeded", "failed", "cancelled", "reconciliation_required"]);

type Decision = "approve" | "reject";

type Frozen = {
  operationId: string;
  approvalRef: string;
  decision: Decision;
  reason: string;
  waitToken: string;
  expectedRevision: number | null;
};

type Attempt =
  | { phase: "idle" }
  | { phase: "confirming"; frozen: Frozen }
  | { phase: "submitting"; frozen: Frozen }
  | {
      phase: "settled";
      frozen: Frozen;
      decisionFacts: ApprovalView["decision"] | null;
      resume: DecisionSubmitResponse["resume"];
      failure: { code: string | null; message: string; unknownResult: boolean } | null;
    };

function newOperationId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `approval-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
}

export function TaskApprovalPanel({
  taskId,
  detail,
  onIssued,
}: {
  taskId: string;
  detail: TaskDetail;
  /** Called once per accepted submission so the page can re-query facts. */
  onIssued?: () => void;
}) {
  return <TaskApprovalContent key={taskId} taskId={taskId} detail={detail} onIssued={onIssued} />;
}

function TaskApprovalContent({
  taskId,
  detail,
  onIssued,
}: {
  taskId: string;
  detail: TaskDetail;
  onIssued?: () => void;
}) {
  const [view, setView] = useState<ApprovalView | null>(null);
  const [viewError, setViewError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState<Attempt>({ phase: "idle" });
  const [reason, setReason] = useState("");
  const requestSeq = useRef(0);
  const inFlight = useRef(false);
  const [viewVersion, setViewVersion] = useState(0);
  const [now, setNow] = useState(Date.now);
  useEffect(() => () => { requestSeq.current += 1; }, []);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    let active = true;
    const rawContract = detail.wait?.contract_ref;
    if (!rawContract || typeof rawContract !== "object" || !("approval_ref" in rawContract)) {
      setView(null); setViewError(null);
      return;
    }
    taskApprovalApi
      .view(taskId)
      .then((data) => {
        if (active) { setView(data); setViewError(null); }
      })
      .catch((err) => {
        if (active) setViewError(taskErrorMessage(err));
      });
    return () => {
      active = false;
    };
  }, [taskId, detail.revision, detail.wait, viewVersion]);

  const refreshView = useCallback(() => setViewVersion((value) => value + 1), []);

  const objectOf = (value: unknown): Record<string, unknown> =>
    typeof value === "object" && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : {};
  const rawWait = objectOf(detail.wait);
  const contractRef = objectOf(rawWait.contract_ref);
  const isApprovalWait =
    detail.lifecycle_state === "waiting_approval" &&
    typeof contractRef.approval_ref === "string" &&
    contractRef.approval_ref.trim() !== "";
  const token = typeof rawWait.wait_token === "string" ? rawWait.wait_token : "";
  const consumed = rawWait.consumed === true;
  const revision = typeof detail.revision === "number" ? detail.revision : null;

  const frozenView = view?.contract ?? null;
  const supported =
    frozenView !== null &&
    typeof frozenView.approval_ref === "string" &&
    frozenView.approval_ref.trim() !== "" &&
    typeof frozenView.tool_name === "string" &&
    frozenView.tool_name.trim() !== "";
  const viewMatches = supported && frozenView?.approval_ref === contractRef.approval_ref &&
    view?.wait?.wait_token === token && view.revision === revision;
  const expires = view?.wait?.expires_at;
  const authorityExpires = view?.authority?.expires_at;
  const expired = [expires, authorityExpires].some((value) => value != null &&
    (!Number.isFinite(Date.parse(value)) || Date.parse(value) <= now));
  const writable = isApprovalWait && rawWait.wake_kind === "resume" && rawWait.consumed === false &&
    token.trim() !== "" && Number.isInteger(revision) && (revision ?? -1) >= 0 && viewMatches &&
    view?.can_decide === true && !expired;

  function readOnlyReason(): string {
    if (detail.lifecycle_state === "waiting_approval") {
      if (!isApprovalWait) return "审批等待记录缺少可核验的审批引用，本页面只读。";
      if (consumed) return "审批等待已消费：决定已生效，任务正在沿恢复路径继续。";
      if (!supported) return "审批契约未知或不受支持，本页面只读。";
      if (!viewMatches) return "等待信息正在更新或版本不一致，请刷新后再决定。";
      if (expired) return "原始审批或等待期限已到期，本页面只读；有效性由服务端判定。";
      if (view?.can_decide !== true) return view?.read_only_reason === "SELF_APPROVAL"
        ? "任务发起人不能自审。" : `当前不可决定审批（${view?.read_only_reason ?? "权限或绑定不可用"}）。`;
      if (token.trim() === "") return "等待记录缺少有效令牌，本页面只读。";
    }
    if (!TERMINAL_LIFECYCLE_STATES.has(detail.lifecycle_state)) {
      return `任务当前为 ${lifecycleLabel(detail.lifecycle_state)}，没有待决定的审批。`;
    }
    return "";
  }

  async function submit(frozen: Frozen, replay = false) {
    if (inFlight.current) return;
    if (!replay && (!writable || frozen.approvalRef !== contractRef.approval_ref ||
      frozen.waitToken !== token || frozen.expectedRevision !== revision)) {
      setAttempt({ phase: "idle" });
      refreshView();
      return;
    }
    const seq = requestSeq.current;
    inFlight.current = true;
    setAttempt({ phase: "submitting", frozen });
    try {
      const response = await taskApprovalApi.submit(taskId, {
        operation_id: frozen.operationId,
        approval_ref: frozen.approvalRef,
        decision: frozen.decision,
        reason: frozen.reason,
        wait_token: frozen.waitToken,
        expected_revision: frozen.expectedRevision,
      });
      if (seq !== requestSeq.current) return;
      setAttempt({
        phase: "settled",
        frozen,
        decisionFacts: {
          operation_id: response.decision.operation_id,
          decision: response.decision.decision,
          reason: response.decision.reason,
          decided_by: response.decision.decided_by,
          created_at: response.decision.created_at ?? null,
          resume_command_id: response.decision.resume_command_id ?? null,
          resume_receipt: response.resume && "command_id" in response.resume ? response.resume : null,
          replay: response.decision.replay,
        },
        resume: response.resume,
        failure: null,
      });
      onIssued?.();
      refreshView();
    } catch (err) {
      if (seq !== requestSeq.current) return;
      const code = taskErrorCode(err);
      setAttempt({
        phase: "settled",
        frozen,
        decisionFacts: null,
        resume: null,
        failure: { code, message: taskErrorMessage(err), unknownResult: code === null },
      });
    } finally {
      inFlight.current = false;
    }
  }

  function decide(decision: Decision) {
    if (!writable || !frozenView?.approval_ref || revision === null) return;
    const trimmed = reason.trim().slice(0, APPROVAL_DECISION_REASON_LIMIT);
    setAttempt({
      phase: "confirming",
      frozen: {
        operationId: newOperationId(),
        approvalRef: frozenView.approval_ref,
        decision,
        reason: trimmed,
        waitToken: token,
        expectedRevision: revision,
      },
    });
  }

  function retry() {
    if (attempt.phase !== "settled") return;
    void submit(attempt.frozen, true);
  }

  const settled = attempt.phase === "settled" ? attempt : null;
  const conflictCodes = new Set(["ALREADY_DECIDED", "OPERATION_CONFLICT", "SELF_APPROVAL", "APPROVAL_REF_MISMATCH"]);

  return (
    <div className="space-y-2" data-testid="task-approval-panel">
      {viewError && <div role="alert" data-testid="task-approval-view-error">
        {viewError}<Button variant="outline" size="sm" onClick={refreshView}>重试读取审批</Button>
      </div>}
      {detail.lifecycle_state === "waiting_approval" && !writable && (
        <p className="text-sm text-muted-foreground" data-testid="task-approval-read-only">{readOnlyReason()}</p>
      )}
      {isApprovalWait && frozenView && viewMatches && (
        <div className="rounded border p-3" data-testid="task-approval-wait-info">
          <p className="text-sm" data-testid="task-approval-wait-summary">
            {consumed ? "审批等待已消费（只读）。" : `待审批动作：${frozenView.tool_name ?? "未知工具"}`}
            {frozenView.risk_level ? `（风险 ${frozenView.risk_level}）` : ""}
          </p>
          {(frozenView.target_resources?.length ?? 0) > 0 && (
            <p className="text-xs text-muted-foreground" data-testid="task-approval-target-resources">
              目标资源：{frozenView.target_resources.join("、")}
            </p>
          )}
          <p className="text-xs text-muted-foreground" data-testid="task-approval-wait-validity">
            {view?.wait?.expires_at
              ? `原始期限至 ${new Date(view.wait.expires_at).toLocaleString()}（页面时钟仅作提示，有效性由服务端判定）。`
              : "未声明原始期限（有效性由服务端判定）。"}
          </p>
        </div>
      )}

      {view?.decision && (
        <div className="space-y-1 rounded border p-3" data-testid="task-approval-decision-record">
          <p className="text-sm">
            已记录决定：
            <strong data-testid="task-approval-decision-state">{view.decision.decision}</strong>
            <span className="text-xs text-muted-foreground"> 由 {view.decision.decided_by} 记录</span>
          </p>
          {view.decision.reason && (
            <p className="text-xs text-muted-foreground" data-testid="task-approval-decision-reason">
              理由：{view.decision.reason}
            </p>
          )}
        </div>
      )}

      {!isApprovalWait && !TERMINAL_LIFECYCLE_STATES.has(detail.lifecycle_state) && (
        <p className="text-sm text-muted-foreground" data-testid="task-approval-not-waiting">
          {readOnlyReason()}
        </p>
      )}

      {writable && (attempt.phase === "idle" || attempt.phase === "confirming") && (
        <div className="space-y-2 rounded border p-3" data-testid="task-approval-form">
          <p className="text-sm">审批决定将绑定当前任务版本（revision {revision}）；只有服务端授权的 ADMIN 可提交，任务发起人不能自审。</p>
          <Input
            value={reason}
            disabled={attempt.phase === "confirming"}
            maxLength={APPROVAL_DECISION_REASON_LIMIT}
            placeholder="决定理由（可选）"
            data-testid="task-approval-reason"
            onChange={(event) => setReason(event.target.value)}
          />
          {attempt.phase === "confirming" && (
            <p className="text-sm" data-testid="task-approval-confirm">
              确认以 {attempt.frozen.decision === "approve" ? "批准" : "拒绝"}提交决定？applied 回执仅表示平台已记录决定并接受恢复命令，不代表受保护动作已执行或任务成功。
            </p>
          )}
          <div className="flex gap-2">
            <Button
              size="sm"
              data-testid="task-approval-approve"
              disabled={attempt.phase === "confirming"}
              onClick={() => decide("approve")}
            >
              批准
            </Button>
            <Button
              variant="outline"
              size="sm"
              data-testid="task-approval-reject"
              disabled={attempt.phase === "confirming"}
              onClick={() => decide("reject")}
            >
              拒绝
            </Button>
            {attempt.phase === "confirming" && (
              <>
                <Button
                  size="sm"
                  data-testid="task-approval-confirm-accept"
                  onClick={() => void submit(attempt.frozen)}
                >
                  确认
                </Button>
                <Button variant="outline" size="sm" data-testid="task-approval-confirm-cancel" onClick={() => setAttempt({ phase: "idle" })}>
                  返回
                </Button>
              </>
            )}
          </div>
        </div>
      )}

      {attempt.phase === "submitting" && (
        <p className="text-sm" data-testid="task-approval-submitting">正在提交审批决定…</p>
      )}

      {settled && settled.failure && (
        <div className="space-y-2 rounded border p-3" data-testid="task-approval-failure" role="alert">
          <p className="text-sm">
            {settled.failure.code && `[${settled.failure.code}] `}
            {settled.failure.message}
          </p>
          {settled.failure.unknownResult && (
            <p className="text-xs text-muted-foreground">
              请求结果未知（网络错误或中断）。重试将复用同一操作 ID 与请求体，不会产生第二个决定。
            </p>
          )}
          <div className="flex items-center gap-2">
            {settled.failure.unknownResult ? (
              <Button variant="outline" size="sm" data-testid="task-approval-retry" onClick={retry}>
                重试提交
              </Button>
            ) : (
              <span className="text-xs text-muted-foreground">
                {conflictCodes.has(settled.failure.code ?? "")
                  ? "服务端拒绝了该操作（冲突或越权），重试不会改变结果。"
                  : "任务已不在可决定状态，重试已停止。"}
              </span>
            )}
            <span className="font-mono text-xs" data-testid="task-approval-operation-id">
              {settled.frozen.operationId}
            </span>
          </div>
        </div>
      )}

      {settled && writable && (settled.frozen.waitToken !== token ||
        (settled.failure !== null && !settled.failure.unknownResult)) && (
        <Button variant="outline" size="sm" data-testid="task-approval-new-operation" onClick={() => {
          setReason(""); setAttempt({ phase: "idle" });
        }}>处理当前审批</Button>
      )}
      {settled && !settled.failure && settled.decisionFacts && (
        <div className="space-y-1 rounded border p-3" data-testid="task-approval-receipt">
          <p className="text-sm">
            决定已记录：
            <strong data-testid="task-approval-receipt-decision">{settled.decisionFacts.decision}</strong>
            <span className="text-xs text-muted-foreground">（操作 {settled.frozen.operationId}）</span>
          </p>
          {settled.resume && (
            <p className="text-sm">
              恢复命令回执：
              <strong data-testid="task-approval-resume-state">
                {settled.resume.state === "pending_recovery" ? "等待恢复" : commandStateLabel(settled.resume.state)}
              </strong>
            </p>
          )}
          {settled.resume?.detail && (
            <p className="text-xs" data-testid="task-approval-resume-detail">{settled.resume.detail}</p>
          )}
          <p className="text-xs text-muted-foreground">
            applied 的恢复回执仅表示平台接受已记录的决定并放行恢复；受保护动作是否执行、任务是否成功，以刷新后的任务与运行记录为准。
          </p>
        </div>
      )}
    </div>
  );
}
