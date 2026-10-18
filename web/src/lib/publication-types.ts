// Agent publication history types and API helpers (step11 read-only slice).
// Built on the exported `api` singleton so auth refresh is reused; this file
// deliberately does not modify lib/api-client.ts.

import { api } from "@/lib/api-client";

/** Whitelisted publication receipt, mirroring AgentPublicationReadSchema. */
export interface PublicationItem {
  id: string;
  agent_id: string;
  version: number;
  agent_config_hash: string;
  candidate_run_id: string | null;
  candidate_run_hash: string | null;
  baseline_run_id: string | null;
  baseline_run_hash: string | null;
  gate_config: Record<string, unknown> | null;
  gate_report: Record<string, unknown> | null;
  // The backend closes this to four values, but the UI deliberately treats it
  // as open: an unknown value is shown verbatim, never mapped onto a known one.
  outcome: string;
  operator_user_id: string | null;
  created_at: string;
}

export interface PublicationListResponse {
  items: PublicationItem[];
  total: number;
}

export const PUBLICATION_LIST_PAGE_SIZE = 20;

export const publicationApi = {
  list(agentId: string, offset = 0, limit = PUBLICATION_LIST_PAGE_SIZE) {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    return api.get<PublicationListResponse>(
      `/api/agents/${agentId}/publications?${params.toString()}`
    );
  },
};

/** Error body thrown by the API client on non-2xx (FastAPI detail envelope). */
export interface PublicationErrorBody {
  detail?: { error?: { code?: string; message?: string } };
}

export function publicationErrorMessage(err: unknown): string {
  const body = err as PublicationErrorBody;
  const message = body?.detail?.error?.message;
  if (message) return message;
  return err instanceof Error ? err.message : "请求失败";
}

export function publicationErrorCode(err: unknown): string | null {
  const body = err as PublicationErrorBody;
  return body?.detail?.error?.code ?? null;
}

// ---------------------------------------------------------------------------
// Outcome labels: the single home for presentation wording. The backend
// guarantees a closed four-value enum, but an unknown value falls back to the
// raw string. Absence is shown as "未记录" — never as any kind of "passed".
// ---------------------------------------------------------------------------

export const OUTCOME_LABELS: Record<string, string> = {
  passed: "门禁通过",
  warn_passed: "告警模式放行",
  force_bypassed: "强制放行（绕过门禁）",
  gate_off: "门禁未启用",
};

export function outcomeLabel(outcome: string | null | undefined): string {
  if (!outcome) return "未记录";
  return Object.hasOwn(OUTCOME_LABELS, outcome) ? OUTCOME_LABELS[outcome] : outcome;
}

const OUTCOME_TONES: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  passed: "secondary",
  warn_passed: "default",
  force_bypassed: "destructive",
  gate_off: "outline",
};

export function outcomeTone(outcome: string | null | undefined) {
  const raw = outcome ?? "";
  if (!raw) return "outline" as const;
  return Object.hasOwn(OUTCOME_TONES, raw) ? OUTCOME_TONES[raw] : "default";
}

export function createdLabel(created_at: string | null | undefined): string {
  if (!created_at) return "未记录";
  const date = new Date(created_at);
  return Number.isNaN(date.getTime()) ? created_at : date.toLocaleString();
}
