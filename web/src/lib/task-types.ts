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
  /**
   * Whitelisted delivery-acceptance view recorded with this run's terminal
   * state. Absent means the run has no acceptance record at all (an
   * execution that predates the surface or never evaluated a delivery) —
   * that is NOT the recorded `not_declared` verdict.
   */
  delivery_acceptance?: DeliveryAcceptanceView;
  event_cursor: number | null;
}

export interface AcceptanceCheckItem {
  kind: string | null;
  status: string | null;
  detail?: string;
}

export interface DeliveryAcceptanceView {
  status:
    | "passed"
    | "acceptance_failed"
    | "acceptance_unverified"
    | "not_declared"
    | "uninterpretable";
  /** Acceptance-standard version; null on `not_declared`. */
  version?: number | null;
  declaration_sha256?: string | null;
  checker_version?: string | null;
  content_sha256?: string | null;
  record_sha256?: string | null;
  checks?: AcceptanceCheckItem[];
  final_decision?: { decision_step_id?: string; content_digest?: string };
  evaluated_by?: Record<string, unknown>;
  /** Present on `uninterpretable`: why the stored record cannot be read. */
  reason?: string;
  /** The raw recorded status, when a view was downgraded to uninterpretable. */
  recorded_status?: string;
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

/**
 * Verdict of re-checking one recorded digest. `verified` means only that
 * the stored content still hashes to the recorded value — never that the
 * business goal passed.
 */
export type DigestVerificationStatus = "verified" | "mismatch" | "unverifiable" | "unsupported";

export interface DigestVerification {
  status: DigestVerificationStatus;
  algorithm: string;
  subject: string;
  recorded_digest: string | null;
  computed_digest: string | null;
}

export interface DecisionReference {
  status: "linked" | "not_recorded";
  decision_step_id: string | null;
  content_digest: string | null;
}

export interface ActionEvidenceReceipt {
  task_id: string;
  run_id: string;
  action_key: string;
  execution_id: string | null;
  tool_call_id: string | null;
  tool_name: string | null;
  side_effect_class: string | null;
  arguments_digest: string | null;
  recovery_state: string | null;
  outcome: string | null;
  result_digest: string | null;
  pending_reconciliation: boolean;
  outcome_recorded_at: string | null;
  result_state: "recorded" | "missing" | "unknown";
  arguments_verification: DigestVerification;
  result_verification: DigestVerification;
  decision_ref: DecisionReference;
  read_at: string;
  ledger_source: string;
}

export interface RunActionEvidenceResponse {
  run_id: string;
  task_id: string;
  supported: boolean;
  ledger_source: string;
  receipt?: ActionEvidenceReceipt;
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
  /**
   * One action's evidence receipt. The action key is a caller-defined
   * ledger key, so it travels as an encoded query parameter rather than
   * a path segment.
   */
  getActionEvidence(runId: string, actionKey: string) {
    const params = new URLSearchParams({ action_key: actionKey });
    return api.get<RunActionEvidenceResponse>(`/api/runs/${runId}/action-evidence?${params.toString()}`);
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

export const ACCEPTANCE_LABELS: Record<string, string> = {
  passed: "验收通过",
  acceptance_failed: "验收未通过",
  acceptance_unverified: "验收未能核实",
  not_declared: "未声明验收",
  uninterpretable: "记录无法解释",
};

export function acceptanceLabel(status: string | null | undefined): string {
  if (!status) return "未记录";
  return ACCEPTANCE_LABELS[status] ?? status;
}

export const CHECK_STATUS_LABELS: Record<string, string> = {
  passed: "通过",
  failed: "未通过",
  unverified: "未能核实",
};

export function checkStatusLabel(status: string | null | undefined): string {
  if (!status) return "未记录";
  return CHECK_STATUS_LABELS[status] ?? status;
}

/** Short display form for a digest; the full value stays in the title. */
export function digestPreview(digest: string | null | undefined): string {
  if (!digest) return "未记录";
  return digest.length > 16 ? `${digest.slice(0, 16)}…` : digest;
}
