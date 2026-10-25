"use client";

// Task cancellation with real receipts (step14 intervention slice). One
// confirmed operation owns a stable command_id: network-failure retries and
// duplicate clicks reuse that id and the same request body, so the server
// replays one command instead of minting several. Receipt states render as
// recorded facts — neither an HTTP success nor an `applied` receipt rewrites
// the Task lifecycle, which only a re-queried detail may change. Server-side
// authorization decides; the panel never derives write permission locally.

import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  commandStateLabel, commandStateMeaning, taskCommandsApi, type CommandIssueResponse,
} from "@/lib/task-command-types";
import { lifecycleLabel, taskErrorCode, taskErrorMessage } from "@/lib/task-types";

const TERMINAL_LIFECYCLE_STATES = new Set(["succeeded", "failed", "cancelled"]);

const SCOPE_NOTE =
  "取消是受控命令：排队或等待中的任务通常被应用为已取消；执行中的任务没有协作中止通道，回执保持已请求，任务可能继续运行到自然结果。本操作不承诺强制停止远程宿主。";

type Attempt =
  | { phase: "idle" }
  | { phase: "confirming"; commandId: string; expectedRevision: number }
  | { phase: "submitting"; commandId: string; expectedRevision: number }
  | {
      phase: "settled";
      commandId: string;
      expectedRevision: number;
      receipt: CommandIssueResponse | null;
      // Structured server error or an unknown network result: null receipt
      // with the failure text. Retrying reuses the same commandId + body.
      failure: { code: string | null; message: string; unknownResult: boolean } | null;
    };

function newCommandId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `cmd-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
}

export function TaskCancelPanel({
  taskId,
  lifecycleState,
  revision,
  onIssued,
}: {
  taskId: string;
  lifecycleState: string;
  revision: number | null;
  /** Called once per accepted submission so the page can re-query facts. */
  onIssued?: () => void;
}) {
  // A keyed content instance owns one Task visit; switching Tasks starts a
  // fresh visit with a fresh attempt state.
  return (
    <TaskCancelContent
      key={taskId}
      taskId={taskId}
      lifecycleState={lifecycleState}
      revision={revision}
      onIssued={onIssued}
    />
  );
}

function TaskCancelContent({
  taskId,
  lifecycleState,
  revision,
  onIssued,
}: {
  taskId: string;
  lifecycleState: string;
  revision: number | null;
  onIssued?: () => void;
}) {
  const [attempt, setAttempt] = useState<Attempt>({ phase: "idle" });
  // Invalidate late responses from an unmounted visit (page leave, Task
  // switch) so they neither render a receipt nor fire onIssued afterwards.
  const requestSeq = useRef(0);
  // Double-click protection while a submission is in flight: the second
  // click must not mint a second network attempt for the same operation.
  const inFlight = useRef(false);

  useEffect(() => () => { requestSeq.current += 1; }, []);

  const terminal = TERMINAL_LIFECYCLE_STATES.has(lifecycleState);
  const cancellable = !terminal && revision !== null;
  const settled = attempt.phase === "settled" ? attempt : null;

  async function submit(operation: { commandId: string; expectedRevision: number }) {
    if (inFlight.current || !cancellable) return;
    const { commandId, expectedRevision } = operation;
    const seq = requestSeq.current;
    inFlight.current = true;
    setAttempt({ phase: "submitting", commandId, expectedRevision });
    try {
      const receipt = await taskCommandsApi.issueCancel(taskId, commandId, expectedRevision);
      if (seq !== requestSeq.current) return;
      setAttempt({ phase: "settled", commandId, expectedRevision, receipt, failure: null });
      // The page re-queries the Task and command list; a refresh failure must
      // not erase this receipt, and the panel never infers the Task outcome.
      onIssued?.();
    } catch (err) {
      if (seq !== requestSeq.current) return;
      const code = taskErrorCode(err);
      setAttempt({
        phase: "settled",
        commandId,
        expectedRevision,
        receipt: null,
        failure: {
          code,
          message: taskErrorMessage(err),
          unknownResult: code === null,
        },
      });
    } finally {
      inFlight.current = false;
    }
  }

  function startConfirm() {
    if (!cancellable || revision === null) return;
    // Bind the version the user confirms, and preserve it across page
    // refreshes: the server binds the command ID to the entire request.
    setAttempt({ phase: "confirming", commandId: newCommandId(), expectedRevision: revision });
  }

  function retry() {
    if (attempt.phase !== "settled") return;
    void submit(attempt);
  }

  const revisionRejected =
    settled?.receipt?.state === "rejected" &&
    typeof settled.receipt.detail === "string" &&
    settled.receipt.detail.includes("revision");

  return (
    <div className="space-y-2" data-testid="task-cancel-panel">
      <p className="text-sm text-muted-foreground" data-testid="task-cancel-scope">{SCOPE_NOTE}</p>

      {terminal && (
        <p className="text-sm" data-testid="task-cancel-terminal">
          任务已处于终态（{lifecycleLabel(lifecycleState)}），无需也无法取消。
        </p>
      )}
      {!terminal && revision === null && (
        <p className="text-sm" data-testid="task-cancel-no-revision">
          当前没有可用的任务版本记录（{lifecycleLabel(lifecycleState)}），本页面不支持发起取消；
          请刷新任务或等待其登记执行状态。
        </p>
      )}

      {cancellable && attempt.phase === "idle" && (
        <Button variant="outline" size="sm" data-testid="task-cancel-request" onClick={startConfirm}>
          请求取消任务
        </Button>
      )}

      {cancellable && attempt.phase === "confirming" && (
        <div className="space-y-2 rounded border p-3" data-testid="task-cancel-confirm">
          <p className="text-sm">
            确认对当前任务发起取消命令？命令将绑定确认时的版本（revision {attempt.expectedRevision}）；
            审批、授权与最终效果由服务端判定。
          </p>
          <div className="flex gap-2">
            <Button
              size="sm"
              data-testid="task-cancel-confirm-accept"
              onClick={() => void submit(attempt)}
            >
              确认取消
            </Button>
            <Button
              variant="outline"
              size="sm"
              data-testid="task-cancel-confirm-cancel"
              disabled={attempt.phase !== "confirming"}
              onClick={() => setAttempt({ phase: "idle" })}
            >
              返回
            </Button>
          </div>
        </div>
      )}

      {attempt.phase === "submitting" && (
        <p className="text-sm" data-testid="task-cancel-submitting">
          正在提交取消命令…
        </p>
      )}

      {settled && settled.failure && (
        <div className="space-y-2 rounded border p-3" data-testid="task-cancel-failure" role="alert">
          <p className="text-sm">
            {settled.failure.code && `[${settled.failure.code}] `}
            {settled.failure.message}
          </p>
          {settled.failure.unknownResult && (
            <p className="text-xs text-muted-foreground">
              请求结果未知（网络错误或中断）。重试将复用同一命令 ID 与请求体，不会产生新命令。
            </p>
          )}
          <div className="flex items-center gap-2">
            {cancellable ? (
              <Button variant="outline" size="sm" data-testid="task-cancel-retry" onClick={retry}>
                重试提交
              </Button>
            ) : (
              <span className="text-xs text-muted-foreground">
                任务当前不可取消（终态或无可用版本），重试已停止。
              </span>
            )}
            <span className="font-mono text-xs" data-testid="task-cancel-command-id">
              {settled.commandId}
            </span>
          </div>
        </div>
      )}

      {settled?.receipt && (
        <div className="space-y-1 rounded border p-3" data-testid="task-cancel-receipt">
          <p className="text-sm">
            取消命令回执：
            <strong data-testid="task-cancel-receipt-state">
              {commandStateLabel(settled.receipt.state)}
            </strong>
          </p>
          {commandStateMeaning(settled.receipt.state) && (
            <p className="text-xs text-muted-foreground" data-testid="task-cancel-receipt-meaning">
              {commandStateMeaning(settled.receipt.state)}
            </p>
          )}
          {settled.receipt.detail && (
            <p className="text-xs" data-testid="task-cancel-receipt-detail">{settled.receipt.detail}</p>
          )}
          {revisionRejected && (
            <p className="text-xs" data-testid="task-cancel-revision-hint">
              任务版本已变化：请刷新任务后重新确认取消；本次不会自动换版本重发。
            </p>
          )}
          <p className="font-mono text-xs" data-testid="task-cancel-command-id">{settled.commandId}</p>
          <p className="text-xs text-muted-foreground">
            任务生命周期以刷新后的服务端记录为准，回执状态不改写任务状态。
          </p>
          {cancellable && (
            <Button variant="outline" size="sm" data-testid="task-cancel-new-attempt" onClick={startConfirm}>
              再次请求取消
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
