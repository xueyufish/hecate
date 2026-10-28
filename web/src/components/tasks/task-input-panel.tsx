"use client";

// Human input for a waiting_input task (one submit operation owns a stable
// command_id: retries and duplicate clicks reuse that id and the same body,
// so the server replays one command instead of minting several). The panel
// is writable ONLY for a plain human input wait — a provide_input wait that
// is unconsumed and not a workflow child-result wait; every other waiting
// shape renders read-only with the reason. Receipts render as recorded
// facts: `applied` means the platform accepted the input (a durable input
// revision the controller's next decision consumes) — never that the model
// has consumed it or that the task succeeded. Server-side authorization
// decides; the panel never derives write permission locally.

import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  commandStateLabel,
  commandStateMeaning,
  taskCommandsApi,
  type CommandIssueResponse,
} from "@/lib/task-command-types";
import { lifecycleLabel, taskErrorCode, taskErrorMessage, type TaskDetail } from "@/lib/task-types";

const TERMINAL_LIFECYCLE_STATES = new Set(["succeeded", "failed", "cancelled", "reconciliation_required"]);

type InputWait = {
  wakeKind: string;
  token: string;
  consumed: boolean;
  expiresAt: string | null;
  tool: string | null;
  fields: string[];
  isWorkflowChildWait: boolean;
  supported: boolean;
  prompt: string | null;
};

/** The typed view of TaskDetail["wait"]; absent/unknown shapes read as not writable. */
function inputWaitOf(detail: TaskDetail): InputWait | null {
  const isObject = (value: unknown): value is Record<string, unknown> =>
    typeof value === "object" && value !== null && !Array.isArray(value);
  const raw = detail.wait;
  if (!isObject(raw)) return null;
  const contractRef = isObject(raw.contract_ref) ? raw.contract_ref : {};
  const contract = isObject(contractRef.contract) ? contractRef.contract : {};
  const fields = contract.fields;
  const supported = contractRef.kind === "input" &&
    typeof contractRef.tool === "string" && contractRef.tool.trim() !== "" &&
    Array.isArray(fields) && fields.length > 0 &&
    fields.every((field) => typeof field === "string" && field.trim() !== "" && field !== "approval_ref") &&
    new Set(fields).size === fields.length &&
    (contract.prompt === undefined || typeof contract.prompt === "string");
  return {
    wakeKind: typeof raw.wake_kind === "string" ? raw.wake_kind : "",
    token: typeof raw.wait_token === "string" ? raw.wait_token : "",
    consumed: raw.consumed === true,
    expiresAt: typeof raw.wait_expires_at === "string" ? raw.wait_expires_at : null,
    tool: typeof contractRef.tool === "string" ? contractRef.tool : null,
    fields: Array.isArray(contract.fields) ? contract.fields.filter((f): f is string => typeof f === "string") : [],
    isWorkflowChildWait: contractRef.await_task_ref != null,
    supported: supported && raw.consumed === false &&
      (raw.wait_expires_at == null || typeof raw.wait_expires_at === "string"),
    prompt: typeof contract.prompt === "string" ? contract.prompt : null,
  };
}

type Operation = {
  commandId: string;
  expectedRevision: number;
  payload: Record<string, unknown>;
  waitToken: string;
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

type ParsedInput = { ok: true; value: Record<string, unknown> } | { ok: false; error: string };

function parseJsonObject(text: string): ParsedInput {
  const trimmed = text.trim();
  if (!trimmed) return { ok: false, error: "请输入非空的 JSON 对象。" };
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    return { ok: false, error: "不是合法的 JSON 文本。" };
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return { ok: false, error: "输入必须是 JSON 对象（键值对），不是数组或标量。" };
  }
  return { ok: true, value: parsed as Record<string, unknown> };
}

export function TaskInputPanel({
  taskId,
  detail,
  onIssued,
}: {
  taskId: string;
  detail: TaskDetail;
  /** Called once per accepted submission so the page can re-query facts. */
  onIssued?: () => void;
}) {
  return <TaskInputContent key={taskId} taskId={taskId} detail={detail} onIssued={onIssued} />;
}

function TaskInputContent({
  taskId,
  detail,
  onIssued,
}: {
  taskId: string;
  detail: TaskDetail;
  onIssued?: () => void;
}) {
  const wait = inputWaitOf(detail);
  const [text, setText] = useState("");
  const [parseError, setParseError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState<Attempt>({ phase: "idle" });
  const requestSeq = useRef(0);
  const inFlight = useRef(false);
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const expires = wait?.expiresAt != null ? Date.parse(wait.expiresAt) : null;
  const expired = expires !== null && (!Number.isFinite(expires) || expires <= now);
  const hasRevision = Number.isInteger(detail.revision) && (detail.revision ?? -1) >= 0;

  useEffect(() => () => { requestSeq.current += 1; }, []);

  const waitingInput = detail.lifecycle_state === "waiting_input";
  const writable =
    waitingInput &&
    wait !== null &&
    wait.wakeKind === "provide_input" &&
    !wait.consumed &&
    !wait.isWorkflowChildWait &&
    wait.supported &&
    !expired &&
    wait.token.trim() !== "" &&
    hasRevision;

  function readOnlyReason(): string {
    if (waitingInput && wait === null) return "任务在等待输入，但等待记录缺失或形状未知，无法在本页面提交。";
    if (waitingInput && wait !== null) {
      if (wait.isWorkflowChildWait)
        return "这是工作流子任务结果等待，只能由平台核验回调推进，人工输入表单不生效。";
      if (wait.wakeKind !== "provide_input") return `这是${wait.wakeKind === "resume" ? "审批" : "其他"}等待，不是人工输入等待。`;
      if (wait.consumed) return "等待已被消费：补充的输入已记录，任务正在继续执行。";
      if (wait.token.trim() === "") return "等待记录缺少有效令牌，无法在本页面提交。";
      if (!wait.supported) return "等待契约未知或不受支持，本页面只读。";
      if (expired) return "等待令牌已到期或期限无效，本页面只读；有效性仍由服务端判定。";
      if (!hasRevision) return "任务版本记录不可用，请刷新后再提交。";
    }
    if (!waitingInput && !TERMINAL_LIFECYCLE_STATES.has(detail.lifecycle_state))
      return `任务当前为 ${lifecycleLabel(detail.lifecycle_state)}，尚无待补充的输入请求。`;
    return "";
  }

  async function submit(operation: Operation, replay = false) {
    if (inFlight.current) return;
    // Retrying an unknown result resolves the original receipt even if a
    // refresh has already advanced the task to another wait or terminal.
    if (!replay && (!writable || operation.waitToken !== wait?.token || operation.expectedRevision !== detail.revision)) {
      setAttempt({ phase: "idle" });
      setParseError("等待信息已变化，请按最新请求重新确认输入。");
      return;
    }
    const seq = requestSeq.current;
    inFlight.current = true;
    setAttempt({ ...operation, phase: "submitting" });
    try {
      const receipt = await taskCommandsApi.issueProvideInput(
        taskId,
        operation.commandId,
        operation.expectedRevision,
        operation.payload,
        operation.waitToken,
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
        failure: { code, message: taskErrorMessage(err), unknownResult: code === null },
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
    setAttempt({ phase: "ready", commandId: newCommandId(), expectedRevision: detail.revision, payload: parsed.value, waitToken: wait!.token });
  }

  function retry() {
    if (attempt.phase !== "settled") return;
    void submit(attempt, true);
  }

  const revisionRejected =
    attempt.phase === "settled" &&
    attempt.receipt?.state === "rejected" &&
    typeof attempt.receipt.detail === "string" &&
    (attempt.receipt.detail.includes("revision") || attempt.receipt.detail.includes("contract"));

  return (
    <div className="space-y-2" data-testid="task-input-panel">
      {!waitingInput && !TERMINAL_LIFECYCLE_STATES.has(detail.lifecycle_state) && (
        <p className="text-sm text-muted-foreground" data-testid="task-input-not-waiting">
          {readOnlyReason()}
        </p>
      )}

      {waitingInput && !writable && (
        <p className="text-sm text-muted-foreground" data-testid="task-input-read-only">{readOnlyReason()}</p>
      )}
      {detail.lifecycle_state === "waiting_approval" && (
        <p className="text-sm text-muted-foreground" data-testid="task-input-read-only">这是审批等待，只能通过核验审批推进。</p>
      )}
      {waitingInput && wait !== null && (
        <div className="rounded border p-3" data-testid="task-input-wait-info">
          <p className="text-sm" data-testid="task-input-wait-summary">
            {wait.isWorkflowChildWait
              ? "工作流子任务结果等待（只读）。"
              : wait.wakeKind === "provide_input" && !wait.consumed
                ? `任务等待人工输入${wait.tool ? `（工具 ${wait.tool}）` : ""}。`
                : wait.wakeKind === "resume"
                  ? "这是审批等待，不是人工输入等待（只读）。"
                  : "等待已消费或非人工输入等待（只读）。"}
          </p>
          {wait.prompt && <p className="whitespace-pre-wrap text-sm" data-testid="task-input-prompt">{wait.prompt}</p>}
          {wait.fields.length > 0 && (
            <p className="text-xs text-muted-foreground" data-testid="task-input-wait-fields">
              请求字段：{wait.fields.join("、")}
            </p>
          )}
          <p className="text-xs text-muted-foreground" data-testid="task-input-wait-validity">
            {wait.expiresAt
              ? `有效期至 ${new Date(wait.expiresAt).toLocaleString()}（页面时钟仅作提示，有效性由服务端判定）。`
              : "未声明有效期（不生成本地期限，有效性由服务端判定）。"}
          </p>
        </div>
      )}

      {writable && (attempt.phase === "idle" || attempt.phase === "ready") && (
        <div className="space-y-2 rounded border p-3" data-testid="task-input-form">
          <p className="text-sm">补充任务请求的信息（JSON 对象，键值对）：</p>
          <textarea
            className="w-full rounded border p-2 font-mono text-xs"
            rows={4}
            disabled={attempt.phase === "ready"}
            data-testid="task-input-textarea"
            placeholder={'{"region": "EU"}'}
            value={text}
            onChange={(event) => setText(event.target.value)}
          />
          {parseError && (
            <p className="text-sm text-red-600" data-testid="task-input-parse-error">
              {parseError}
            </p>
          )}
          {attempt.phase === "ready" && (
            <p className="text-sm" data-testid="task-input-confirm">
              确认提交补充输入？命令绑定当前任务版本（revision {attempt.expectedRevision}）；applied 回执仅表示平台已接受输入，不代表模型已消费或任务成功。
            </p>
          )}
          <div className="flex gap-2">
            {attempt.phase === "idle" ? (
              <Button size="sm" data-testid="task-input-parse" onClick={confirm}>
                校验输入
              </Button>
            ) : (
              <>
                <Button size="sm" data-testid="task-input-confirm-accept" onClick={() => void submit(attempt)}>
                  确认提交
                </Button>
                <Button variant="outline" size="sm" data-testid="task-input-confirm-cancel" onClick={() => setAttempt({ phase: "idle" })}>
                  返回修改
                </Button>
              </>
            )}
          </div>
        </div>
      )}

      {attempt.phase === "submitting" && (
        <p className="text-sm" data-testid="task-input-submitting">
          正在提交补充输入…
        </p>
      )}

      {attempt.phase === "settled" && attempt.failure && (
        <div className="space-y-2 rounded border p-3" data-testid="task-input-failure" role="alert">
          <p className="text-sm">
            {attempt.failure.code && `[${attempt.failure.code}] `}
            {attempt.failure.message}
          </p>
          {attempt.failure.unknownResult && (
            <p className="text-xs text-muted-foreground">
              请求结果未知（网络错误或中断）。重试将复用同一命令 ID 与请求体，不会产生新命令。
            </p>
          )}
          <div className="flex items-center gap-2">
            {attempt.failure.unknownResult ? (
              <Button variant="outline" size="sm" data-testid="task-input-retry" onClick={retry}>
                重试提交
              </Button>
            ) : (
              <span className="text-xs text-muted-foreground">请按最新等待信息重新填写输入。</span>
            )}
            <span className="font-mono text-xs" data-testid="task-input-command-id">
              {attempt.commandId}
            </span>
          </div>
        </div>
      )}

      {attempt.phase === "settled" && writable && (
        (attempt.receipt !== null && attempt.receipt.state !== "applied") ||
        (attempt.receipt === null && !attempt.failure?.unknownResult) || attempt.waitToken !== wait?.token
      ) && (
        <Button variant="outline" size="sm" data-testid="task-input-new-operation" onClick={() => {
          setText("");
          setParseError(null);
          setAttempt({ phase: "idle" });
        }}>填写新的输入</Button>
      )}
      {attempt.phase === "settled" && attempt.receipt && (
        <div className="space-y-1 rounded border p-3" data-testid="task-input-receipt">
          <p className="text-sm">
            补充输入命令回执：
            <strong data-testid="task-input-receipt-state">{commandStateLabel(attempt.receipt.state)}</strong>
          </p>
          {commandStateMeaning(attempt.receipt.state) && (
            <p className="text-xs text-muted-foreground" data-testid="task-input-receipt-meaning">
              {commandStateMeaning(attempt.receipt.state)}
            </p>
          )}
          {attempt.receipt.detail && (
            <p className="text-xs" data-testid="task-input-receipt-detail">
              {attempt.receipt.detail}
            </p>
          )}
          {revisionRejected && (
            <p className="text-xs" data-testid="task-input-revision-hint">
              任务版本已变化或等待契约未满足：请刷新任务后按最新等待信息重新提交；本次不会自动换版本重发。
            </p>
          )}
          <p className="font-mono text-xs" data-testid="task-input-command-id">{attempt.commandId}</p>
          <p className="text-xs text-muted-foreground">
            applied 仅表示输入已被平台接受为持久修订；模型是否消费、任务是否成功，以刷新后的任务与运行记录为准。
          </p>
        </div>
      )}
    </div>
  );
}
