"use client";

// Live supplement for a RUNNING platform-local controller_loop task (one
// submit operation owns a stable command_id: retries and duplicate clicks
// reuse that id and the same body, so the server replays one command instead
// of minting several). Writability is SERVER-derived from
// TaskDetail["live_input"] — the panel never derives permission locally, and
// unsupported shapes render that explicit reason. Receipts render as recorded
// facts: `applied` plus delivery "已接收" means the platform durably received
// the pending revision — never that it took effect, and never that the model
// consumed it; consumption shows up in the command list's delivery facts.

import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  commandStateLabel,
  commandStateMeaning,
  liveDeliveryStatusLabel,
  liveDeliveryStatusMeaning,
  taskCommandsApi,
  type CommandIssueResponse,
} from "@/lib/task-command-types";
import { taskErrorCode, taskErrorMessage, type TaskDetail } from "@/lib/task-types";

type Operation = {
  commandId: string;
  expectedRevision: number;
  content: Record<string, unknown>;
};

type Attempt =
  | { phase: "idle" }
  | ({ phase: "ready" } & Operation)
  | ({ phase: "submitting" } & Operation)
  | (Operation & {
      phase: "settled";
      receipt: CommandIssueResponse | null;
      failure: { code: string | null; message: string; unknownResult: boolean } | null;
    });

function newCommandId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `cmd-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
}

function parseJsonObject(text: string): { ok: true; value: Record<string, unknown> } | { ok: false; error: string } {
  const trimmed = text.trim();
  if (!trimmed) return { ok: false, error: "请输入非空的 JSON 对象。" };
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    return { ok: false, error: "不是合法的 JSON 文本。" };
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed) || Object.keys(parsed).length === 0) {
    return { ok: false, error: "补充内容必须是 JSON 对象（键值对），不是数组或标量。" };
  }
  if ("approval_ref" in parsed || "wait_token" in parsed || "agent_child_result" in parsed) {
    return { ok: false, error: "补充内容不能包含审批、等待令牌或子结果控制字段。" };
  }
  return { ok: true, value: parsed as Record<string, unknown> };
}

export function TaskLiveInputPanel({
  taskId,
  detail,
  onIssued,
}: {
  taskId: string;
  detail: TaskDetail;
  /** Called once per accepted submission so the page can re-query facts. */
  onIssued?: () => void;
}) {
  return <TaskLiveInputContent key={taskId} taskId={taskId} detail={detail} onIssued={onIssued} />;
}

function TaskLiveInputContent({
  taskId,
  detail,
  onIssued,
}: {
  taskId: string;
  detail: TaskDetail;
  onIssued?: () => void;
}) {
  const capability = detail.live_input;
  const [text, setText] = useState("");
  const [parseError, setParseError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState<Attempt>({ phase: "idle" });
  const requestSeq = useRef(0);
  const inFlight = useRef(false);
  const hasRevision = Number.isInteger(detail.revision) && (detail.revision ?? -1) >= 0;

  useEffect(() => () => { requestSeq.current += 1; }, []);

  if (capability === undefined) {
    // No live-input surface on this task at all (non-controller or legacy).
    return null;
  }
  const writable = capability.supported && hasRevision;

  async function submit(operation: Operation, replay = false) {
    if (inFlight.current) return;
    // Retrying an unknown result resolves the original receipt even if a
    // refresh has already advanced the task's state.
    if (!replay && (!writable || operation.expectedRevision !== detail.revision)) {
      setAttempt({ phase: "idle" });
      setParseError("任务状态已变化，请刷新后重新提交。");
      return;
    }
    const seq = requestSeq.current;
    inFlight.current = true;
    setAttempt({ ...operation, phase: "submitting" });
    try {
      const receipt = await taskCommandsApi.issueLiveInput(
        taskId,
        operation.commandId,
        operation.expectedRevision,
        operation.content,
      );
      if (seq !== requestSeq.current) return;
      setAttempt({ ...operation, phase: "settled", receipt, failure: null });
      onIssued?.();
    } catch (err) {
      if (seq !== requestSeq.current) return;
      const code = taskErrorCode(err);
      setAttempt({
        ...operation,
        phase: "settled",
        receipt: null,
        failure: { code, message: taskErrorMessage(err), unknownResult: code === null || code === "TASK_CONTROL_ERROR" },
      });
    } finally {
      inFlight.current = false;
    }
  }

  function confirm() {
    if (!writable || detail.revision === null) return;
    const parsed = parseJsonObject(text);
    if (!parsed.ok) {
      setParseError(parsed.error);
      return;
    }
    setParseError(null);
    setAttempt({ phase: "ready", commandId: newCommandId(), expectedRevision: detail.revision, content: parsed.value });
  }

  function retry() {
    if (attempt.phase !== "settled") return;
    void submit(attempt, true);
  }

  return (
    <div className="space-y-2" data-testid="task-live-input-panel">
      {!capability.supported && (
        <p className="text-sm text-muted-foreground" data-testid="task-live-input-unsupported">
          {capability.reason ?? "当前任务形态不支持运行中补充输入。"}
        </p>
      )}

      {writable && (attempt.phase === "idle" || attempt.phase === "ready") && (
        <div className="space-y-2 rounded border p-3" data-testid="task-live-input-form">
          <p className="text-sm">任务运行中补充要求（JSON 对象，键值对）：</p>
          <p className="text-xs text-muted-foreground">
            要求先持久接收，在下一次尚未开始的决策边界生效；已开始的模型调用与已接受的决策按原事实继续。
          </p>
          <textarea
            className="w-full rounded border p-2 font-mono text-xs"
            rows={4}
            disabled={attempt.phase === "ready"}
            data-testid="task-live-input-textarea"
            placeholder={'{"region": "EU"}'}
            value={text}
            onChange={(event) => setText(event.target.value)}
          />
          {parseError && (
            <p className="text-sm text-red-600" data-testid="task-live-input-parse-error">
              {parseError}
            </p>
          )}
          {attempt.phase === "ready" && (
            <p className="text-sm" data-testid="task-live-input-confirm">
              确认提交补充要求？命令绑定当前任务版本（revision {attempt.expectedRevision}）；接收成功不代表要求已生效或已被消费。
            </p>
          )}
          <div className="flex gap-2">
            {attempt.phase === "idle" ? (
              <Button size="sm" data-testid="task-live-input-parse" onClick={confirm}>
                校验内容
              </Button>
            ) : (
              <>
                <Button size="sm" data-testid="task-live-input-confirm-accept" onClick={() => void submit(attempt)}>
                  确认提交
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  data-testid="task-live-input-confirm-cancel"
                  onClick={() => setAttempt({ phase: "idle" })}
                >
                  返回修改
                </Button>
              </>
            )}
          </div>
        </div>
      )}

      {attempt.phase === "submitting" && (
        <p className="text-sm" data-testid="task-live-input-submitting">
          正在提交补充要求…
        </p>
      )}

      {attempt.phase === "settled" && attempt.failure && (
        <div className="space-y-2 rounded border p-3" data-testid="task-live-input-failure" role="alert">
          <p className="text-sm">
            {attempt.failure.code && `[${attempt.failure.code}] `}
            {attempt.failure.message}
          </p>
          {attempt.failure.unknownResult && (
            <p className="text-xs text-muted-foreground">
              暂时无法确认提交结果。重试将复用同一命令 ID 与请求体。
            </p>
          )}
          <div className="flex items-center gap-2">
            {attempt.failure.unknownResult ? (
              <Button variant="outline" size="sm" data-testid="task-live-input-retry" onClick={retry}>
                重试提交
              </Button>
            ) : (
              <span className="text-xs text-muted-foreground">请刷新任务后按最新状态重新提交。</span>
            )}
            <span className="font-mono text-xs" data-testid="task-live-input-command-id">
              {attempt.commandId}
            </span>
          </div>
        </div>
      )}

      {attempt.phase === "settled" && attempt.receipt && (
        <div className="space-y-1 rounded border p-3" data-testid="task-live-input-receipt">
          <p className="text-sm">
            补充要求命令回执：
            <strong data-testid="task-live-input-receipt-state">
              {attempt.receipt.delivery
                ? liveDeliveryStatusLabel(attempt.receipt.delivery.status)
                : attempt.receipt.state === "applied"
                  ? "已接收"
                  : commandStateLabel(attempt.receipt.state)}
            </strong>
          </p>
          <p className="text-xs text-muted-foreground" data-testid="task-live-input-receipt-meaning">
            {attempt.receipt.delivery
              ? liveDeliveryStatusMeaning(attempt.receipt.delivery.status)
              : attempt.receipt.state === "applied"
                ? liveDeliveryStatusMeaning("received")
                : commandStateMeaning(attempt.receipt.state)}
          </p>
          {attempt.receipt.detail && (
            <p className="text-xs" data-testid="task-live-input-receipt-detail">
              {attempt.receipt.detail}
            </p>
          )}
          <p className="font-mono text-xs" data-testid="task-live-input-command-id">{attempt.commandId}</p>
          <p className="text-xs text-muted-foreground">
            已接收仅表示平台已持久接收；是否绑定生效边界、是否被实际消费或终态结算，以命令面板与刷新后的任务为准。
          </p>
          {attempt.receipt.state === "requested" ? (
            <Button variant="outline" size="sm" data-testid="task-live-input-retry" onClick={retry}>
              重查并重试原命令
            </Button>
          ) : (
            <Button variant="outline" size="sm" data-testid="task-live-input-new-operation" onClick={() => {
              setText("");
              setParseError(null);
              setAttempt({ phase: "idle" });
            }}>提交新的补充要求</Button>
          )}
        </div>
      )}
    </div>
  );
}
