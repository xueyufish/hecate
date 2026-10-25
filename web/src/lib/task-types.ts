// Task/Run observation types and API helpers (step14 read-only slice).
// Built on the exported `api` singleton so auth refresh is reused; this file
// deliberately does not modify lib/api-client.ts.

import { api } from "@/lib/api-client";

export interface TaskSummary {
  task_id: string;
  goal: string;
  lifecycle_state: string;
  created_at: string | null;
}

export interface TaskListResponse {
  items: TaskSummary[];
  total: number;
}

export interface RunSummary {
  run_id: string;
  attempt_no: number;
  deployment_id: string;
  backend_ref: Record<string, unknown>;
  projection: Record<string, unknown>;
  event_cursor: number | null;
}

export interface TaskDetail {
  task_id: string;
  goal: string;
  issuer_domain: string;
  workspace_id: string;
  lifecycle_state: string;
  /** Durable-record revision read with this response; null = no record. */
  revision: number | null;
  created_at: string | null;
  runs: RunSummary[];
  wait?: Record<string, unknown>;
}

export interface RunActionItem {
  action_key: string;
  execution_id: string | null;
  tool_call_id: string | null;
  tool_name: string | null;
  arguments_digest: string | null;
  side_effect_class: string | null;
  state: string | null;
  outcome: string | null;
  result_digest: string | null;
  pending_reconciliation: boolean;
}

export interface RunActionsResponse {
  run_id: string;
  task_id: string;
  actions: RunActionItem[];
  ledger_source: string;
  supported: boolean;
}

export const TASK_LIST_PAGE_SIZE = 20;

export const taskApi = {
  list(page = 1, pageSize = TASK_LIST_PAGE_SIZE, state?: string) {
    const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
    if (state) params.set("lifecycle_state", state);
    return api.get<TaskListResponse>(`/api/tasks?${params.toString()}`);
  },
  get(taskId: string) {
    return api.get<TaskDetail>(`/api/tasks/${taskId}`);
  },
  getActions(runId: string) {
    return api.get<RunActionsResponse>(`/api/runs/${runId}/actions`);
  },
};

/** Error body thrown by the API client on non-2xx (FastAPI detail envelope). */
export interface TaskErrorBody {
  detail?: { error?: { code?: string; message?: string } };
}

export function taskErrorMessage(err: unknown): string {
  const body = err as TaskErrorBody;
  const message = body?.detail?.error?.message;
  if (message) return message;
  return err instanceof Error ? err.message : "请求失败";
}

export function taskErrorCode(err: unknown): string | null {
  const body = err as TaskErrorBody;
  return body?.detail?.error?.code ?? null;
}

// ---------------------------------------------------------------------------
// Status label maps: the single home for presentation wording. Unknown values
// fall back to the raw string (a new backend state is shown, never swallowed).
// ---------------------------------------------------------------------------

export const LIFECYCLE_LABELS: Record<string, string> = {
  queued: "已排队",
  running: "执行中",
  waiting_input: "等待输入",
  waiting_approval: "等待审批",
  succeeded: "已成功",
  failed: "失败",
  cancelled: "已取消",
  reconciliation_required: "待对账",
  unrecorded: "未记录",
};

export function lifecycleLabel(state: string | null | undefined): string {
  if (!state) return "未记录";
  return LIFECYCLE_LABELS[state] ?? state;
}

export const RECOVERY_STATE_LABELS: Record<string, string> = {
  never_started: "未开始",
  claimed: "已领取",
  outcome_unknown: "结果未知",
  store_unavailable: "存储不可用",
};

export function recoveryStateLabel(state: string | null | undefined): string {
  if (!state) return "未记录";
  return RECOVERY_STATE_LABELS[state] ?? state;
}

export const OUTCOME_LABELS: Record<string, string> = {
  succeeded: "已成功",
  failed: "失败",
  unknown: "结果未知",
};

/** Outcome is a RECORDED fact; absence means "not recorded", never success. */
export function outcomeLabel(outcome: string | null | undefined): string {
  if (!outcome) return "未记录";
  return OUTCOME_LABELS[outcome] ?? outcome;
}
