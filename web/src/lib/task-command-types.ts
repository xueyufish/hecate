// Task control-command receipt types (step14 read-only slice). Only the paged
// list query is wrapped here: the single-command read performs lazy expiry and
// must not be issued from an observation surface. Built on the exported `api`
// singleton; this file deliberately does not modify lib/api-client.ts.

import { api } from "@/lib/api-client";

/** Opaque logical reference serialized by the backend (BackendRef.to_dict()). */
export interface CommandBackendRef {
  kind: string;
  issuer_domain: string;
  id: string;
}

/** One whitelisted command receipt; `state` comes back verbatim from the list read. */
export interface TaskCommandItem {
  command_id: string;
  kind: string;
  state: string;
  issued_at: string;
  issuer: string;
  task_ref: CommandBackendRef;
  run_ref: CommandBackendRef | null;
  expires_at: string | null;
  expected_revision: number | null;
  /** Whitelisted live-supplement delivery facts; present only for
   * urn:hecate:task-live-input/1 commands. */
  delivery?: LiveInputDelivery;
}

export interface TaskCommandsPage {
  items: TaskCommandItem[];
  total: number;
}

/** Whitelisted delivery facts of one live supplement, derived by the server
 * from the pending revision row and the accepted decision that pinned it.
 * Never carries content, wait tokens or storage internals. */
export interface LiveInputDelivery {
  source_ref: string;
  revision_id: string;
  run_ref: CommandBackendRef;
  status: "received" | "bound" | "consumed" | "not_applied";
  effective_turn?: number;
  consumed_by?: { decision_step_id: string; turn: number | null };
  settled_reason?: string;
}

/** Response of POST /api/tasks/{id}/commands: the receipt the server records. */
export interface CommandIssueResponse {
  command_id: string;
  kind: string;
  state: string;
  issued_at: string;
  issuer: string;
  task_ref: CommandBackendRef;
  run_ref: CommandBackendRef | null;
  expires_at: string | null;
  expected_revision: number | null;
  detail?: string | null;
  /** Present on live-supplement receipts; `received` on success. */
  delivery?: LiveInputDelivery;
}

export const TASK_COMMANDS_PAGE_SIZE = 20;

export const taskCommandsApi = {
  list(taskId: string, limit = TASK_COMMANDS_PAGE_SIZE, offset = 0) {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    return api.get<TaskCommandsPage>(`/api/tasks/${taskId}/commands?${params.toString()}`);
  },
  // The full-command endpoint (not the /cancel shorthand) so the request
  // carries the caller's stable command_id and the page-read revision. The
  // same id + body replays the same server command instead of minting one.
  issueCancel(taskId: string, commandId: string, expectedRevision: number) {
    return api.post<CommandIssueResponse>(`/api/tasks/${taskId}/commands`, {
      kind: "cancel",
      command_id: commandId,
      expected_revision: expectedRevision,
    });
  },
  // Human input for a waiting_input task: the payload is the user's field
  // object (urn:hecate:task-user-input/0 — a JSON object whose keys
  // answer the wait contract's declared fields); the wait token rides
  // detail_ns as caller metadata, exactly like the cancel command carries
  // its revision binding. The same id + body replays the same command.
  issueProvideInput(
    taskId: string,
    commandId: string,
    expectedRevision: number,
    payload: Record<string, unknown>,
    waitToken: string,
  ) {
    return api.post<CommandIssueResponse>(`/api/tasks/${taskId}/commands`, {
      kind: "provide_input",
      command_id: commandId,
      expected_revision: expectedRevision,
      payload,
      payload_schema_ref: payload && Object.keys(payload).length > 0 ? TASK_USER_INPUT_SCHEMA_REF : null,
      detail_ns: { wait_token: waitToken },
    });
  },
  // Live supplement for a RUNNING platform controller task: the payload is
  // the versioned envelope (urn:hecate:task-live-input/1) over the user's
  // content object. The same id + body replays the same command; changing
  // the content requires a new command identity.
  issueLiveInput(taskId: string, commandId: string, expectedRevision: number, content: Record<string, unknown>) {
    return api.post<CommandIssueResponse>(`/api/tasks/${taskId}/commands`, {
      kind: "provide_input",
      command_id: commandId,
      expected_revision: expectedRevision,
      payload: { schema_version: 1, content },
      payload_schema_ref: TASK_LIVE_INPUT_SCHEMA_REF,
    });
  },
};

/** Declared shape of a human provide_input payload: a JSON object of
 * user fields answering the parked contract's `fields`. Consumers: the
 * durable input revision content (task_control._wake_waiting) and the
 * contract-field validation against it. */
export const TASK_USER_INPUT_SCHEMA_REF = "urn:hecate:task-user-input/0";

/** Declared shape of a live-supplement payload: a versioned envelope whose
 * `content` object is the model-visible supplement. The envelope — never
 * raw text — distinguishes a running-task supplement from a waiting_input
 * answer, and the server rejects control keys outright. */
export const TASK_LIVE_INPUT_SCHEMA_REF = "urn:hecate:task-live-input/1";

// ---------------------------------------------------------------------------
// Label maps: the single home for presentation wording, mirroring task-types.
// States render verbatim as recorded by the server; "requested" and
// "acknowledged" are receipt facts and must never read as applied/effective.
// ---------------------------------------------------------------------------

export const COMMAND_STATE_LABELS: Record<string, string> = {
  requested: "已请求",
  acknowledged: "已确认接收",
  applied: "已应用",
  rejected: "已拒绝",
  expired: "已过期",
};

export function commandStateLabel(state: string | null | undefined): string {
  if (!state) return "未记录";
  return Object.hasOwn(COMMAND_STATE_LABELS, state) ? COMMAND_STATE_LABELS[state] : state;
}

/** What each recorded receipt state actually attests — never "已生效". */
export const COMMAND_STATE_MEANINGS: Record<string, string> = {
  requested: "平台已记录命令；执行方尚未确认接收或应用",
  acknowledged: "执行方已确认接收命令；不证明命令已生效",
  applied: "执行方报告命令已实际生效；任务生命周期仍以重新查询为准",
  rejected: "命令被拒绝（如任务已终态、版本不符或能力不支持）",
  expired: "命令在生效前已过期",
};

export function commandStateMeaning(state: string | null | undefined): string | null {
  if (!state) return null;
  return Object.hasOwn(COMMAND_STATE_MEANINGS, state) ? COMMAND_STATE_MEANINGS[state] : null;
}

export const COMMAND_KIND_LABELS: Record<string, string> = {
  cancel: "取消",
  pause: "暂停",
  resume: "恢复",
  provide_input: "补充输入",
};

export function commandKindLabel(kind: string | null | undefined): string {
  if (!kind) return "未记录";
  return Object.hasOwn(COMMAND_KIND_LABELS, kind) ? COMMAND_KIND_LABELS[kind] : kind;
}

// ---------------------------------------------------------------------------
// Live-supplement delivery lifecycle (urn:hecate:task-live-input/1). Each
// status is a server-derived fact; "已接收" and "已绑定边界" must never read
// as consumed, and "未应用" is a terminal settlement, never a retryable state.
// ---------------------------------------------------------------------------

export const LIVE_DELIVERY_STATUS_LABELS: Record<string, string> = {
  received: "已接收",
  bound: "已绑定边界",
  consumed: "已消费",
  not_applied: "未应用",
};

export function liveDeliveryStatusLabel(status: string | null | undefined): string {
  if (!status) return "未记录";
  return Object.hasOwn(LIVE_DELIVERY_STATUS_LABELS, status) ? LIVE_DELIVERY_STATUS_LABELS[status] : status;
}

export const LIVE_DELIVERY_STATUS_MEANINGS: Record<string, string> = {
  received: "平台已持久接收该补充要求；尚未绑定生效边界，等待下一次未开始的决策边界",
  bound: "已绑定到指定决策边界；等待该边界的决策提交并实际消费",
  consumed: "已由某个已接受决策实际消费（见消费位置）",
  not_applied: "任务在消费前到达终态；该要求已显式结算为未应用",
};

export function liveDeliveryStatusMeaning(status: string | null | undefined): string | null {
  if (!status) return null;
  return Object.hasOwn(LIVE_DELIVERY_STATUS_MEANINGS, status) ? LIVE_DELIVERY_STATUS_MEANINGS[status] : null;
}
