// Task approval view + decision API. The GET is observation only (the
// whitelisted wait summary, the recorded decision facts and the resume
// receipt); the POST records one ADMIN decision with a stable operation id
// and drives the resume phase. Built on the exported `api` singleton.

import { api } from "@/lib/api-client";

export interface ApprovalContractSummary {
  approval_ref: string | null;
  tool_name: string | null;
  risk_level: string | null;
  target_resources: string[];
  invocation_id: string | null;
}

export interface ApprovalWaitSummary {
  consumed: boolean;
  expires_at: string | null;
  wait_token: string | null;
}

export interface ApprovalDecisionFacts {
  operation_id: string;
  decision: string;
  reason: string | null;
  decided_by: string;
  created_at: string | null;
  resume_command_id: string | null;
  resume_receipt: CommandReceiptSummary | null;
  replay?: boolean;
}

export interface CommandReceiptSummary {
  command_id: string;
  kind: string;
  state: string;
  issued_at: string;
  issuer: string;
  detail?: string | null;
}

export interface ApprovalView {
  task_id: string;
  workspace_id: string;
  wait: ApprovalWaitSummary;
  contract: ApprovalContractSummary;
  decision: ApprovalDecisionFacts | null;
  revision: number | null;
  can_decide: boolean;
  read_only_reason: string | null;
  authority: { judgment: string; decided_by: string | null; expires_at: string | null } | null;
}

export interface DecisionSubmitResponse {
  decision: {
    operation_id: string;
    decision: string;
    reason: string;
    decided_by: string;
    binding_judgment?: string;
    created_at?: string | null;
    resume_command_id?: string | null;
    replay?: boolean;
  };
  resume: CommandReceiptSummary | { state: "pending_recovery"; detail?: string | null } | null;
}

export const APPROVAL_DECISION_REASON_LIMIT = 2000;

export const taskApprovalApi = {
  view(taskId: string) {
    return api.get<ApprovalView>(`/api/tasks/${taskId}/approval`);
  },
  // The operation id is the caller's stable decision identity: retries after
  // unknown network results reuse it and the same body, so the server replays
  // one decision instead of recording two.
  submit(
    taskId: string,
    body: {
      operation_id: string;
      approval_ref: string;
      decision: "approve" | "reject";
      reason: string;
      wait_token: string;
      expected_revision: number | null;
    },
  ) {
    return api.post<DecisionSubmitResponse>(`/api/tasks/${taskId}/approval`, body);
  },
};
