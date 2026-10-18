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
}

export interface TaskCommandsPage {
  items: TaskCommandItem[];
  total: number;
}

export const TASK_COMMANDS_PAGE_SIZE = 20;

export const taskCommandsApi = {
  list(taskId: string, limit = TASK_COMMANDS_PAGE_SIZE, offset = 0) {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    return api.get<TaskCommandsPage>(`/api/tasks/${taskId}/commands?${params.toString()}`);
  },
};

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
