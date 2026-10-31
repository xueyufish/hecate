import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { StrictMode } from "react";

import { RunActionsPanel } from "@/components/tasks/run-actions-panel";
import type {
  ActionEvidenceReceipt,
  RunActionEvidenceResponse,
  RunActionsResponse,
} from "@/lib/task-types";

const taskApiMock = vi.hoisted(() => ({
  list: vi.fn(),
  get: vi.fn(),
  getActions: vi.fn(),
  getActionEvidence: vi.fn(),
}));

vi.mock("@/lib/task-types", async () => {
  const actual = await vi.importActual<typeof import("@/lib/task-types")>("@/lib/task-types");
  return { ...actual, taskApi: taskApiMock };
});

function actionsResponse(actions: string[]): RunActionsResponse {
  return {
    run_id: "run-1",
    task_id: "task-1",
    actions: actions.map((action_key) => ({
      action_key,
      execution_id: `exec-${action_key}`,
      tool_call_id: `tc-${action_key}`,
      tool_name: "biz_tool",
      arguments_digest: "arg-digest",
      side_effect_class: "non_idempotent_write",
      state: "claimed",
      outcome: "succeeded",
      result_digest: "res-digest",
      pending_reconciliation: false,
    })),
    ledger_source: "core",
    supported: true,
  };
}

function receipt(overrides: Partial<ActionEvidenceReceipt> = {}): ActionEvidenceReceipt {
  return {
    task_id: "task-1",
    run_id: "run-1",
    action_key: "a-1",
    execution_id: "exec-a-1",
    tool_call_id: "tc-a-1",
    tool_name: "biz_tool",
    side_effect_class: "non_idempotent_write",
    arguments_digest: "arg-digest",
    recovery_state: "claimed",
    outcome: "succeeded",
    result_digest: "res-digest",
    pending_reconciliation: false,
    outcome_recorded_at: "2026-10-30T00:00:00+00:00",
    result_state: "recorded",
    arguments_verification: {
      status: "unverifiable",
      algorithm: "sha256:canonical-json",
      subject: "accepted_decision_action_arguments",
      recorded_digest: "arg-digest",
      computed_digest: null,
    },
    result_verification: {
      status: "verified",
      algorithm: "sha256:canonical-json",
      subject: "recorded_result_payload",
      recorded_digest: "res-digest",
      computed_digest: "res-digest",
    },
    decision_ref: { status: "not_recorded", decision_step_id: null, content_digest: null },
    read_at: "2026-10-30T00:01:00+00:00",
    ledger_source: "core",
    ...overrides,
  };
}

function evidenceResponse(overrides: Partial<RunActionEvidenceResponse> = {}): RunActionEvidenceResponse {
  return {
    run_id: "run-1",
    task_id: "task-1",
    supported: true,
    ledger_source: "core",
    receipt: receipt(),
    ...overrides,
  };
}

async function openEvidence(actionIndex = 0) {
  const buttons = await screen.findAllByTestId("action-evidence-open");
  await userEvent.click(buttons[actionIndex]);
}

beforeEach(() => {
  window.history.replaceState({}, "", "/");
  taskApiMock.list.mockReset();
  taskApiMock.get.mockReset();
  taskApiMock.getActions.mockReset();
  taskApiMock.getActionEvidence.mockReset();
  taskApiMock.getActions.mockResolvedValue(actionsResponse(["a-1", "a-2"]));
});

describe("action evidence receipt", () => {
  it("renders the recorded facts, the digest and its verification", async () => {
    taskApiMock.getActionEvidence.mockResolvedValue(evidenceResponse());
    render(<RunActionsPanel runId="run-1" />);
    await openEvidence();

    expect(await screen.findByTestId("evidence-ready")).toBeInTheDocument();
    expect(screen.getByTestId("evidence-task-id")).toHaveTextContent("task-1");
    expect(screen.getByTestId("evidence-run-id")).toHaveTextContent("run-1");
    expect(screen.getByTestId("evidence-action-key")).toHaveTextContent("a-1");
    expect(screen.getByTestId("evidence-tool")).toHaveTextContent("biz_tool");
    expect(screen.getByTestId("evidence-side-effect")).toHaveTextContent("non_idempotent_write");
    expect(screen.getByTestId("evidence-outcome")).toHaveTextContent("succeeded");
    expect(screen.getByTestId("evidence-result-digest")).toHaveTextContent("res-digest");
    expect(screen.getByTestId("evidence-result-verification")).toHaveTextContent("本次核验一致");
    expect(screen.getByTestId("evidence-result-verification")).toHaveTextContent("sha256:canonical-json");
    expect(screen.getByTestId("evidence-result-verification")).toHaveTextContent("recorded_result_payload");
  });

  it("never presents a matching digest as a passed goal", async () => {
    taskApiMock.getActionEvidence.mockResolvedValue(evidenceResponse());
    render(<RunActionsPanel runId="run-1" />);
    await openEvidence();
    expect(await screen.findByTestId("evidence-ready")).toHaveTextContent("不代表业务目标已通过");
  });

  it("presents a digest mismatch distinctly", async () => {
    taskApiMock.getActionEvidence.mockResolvedValue(
      evidenceResponse({
        receipt: receipt({
          result_verification: {
            status: "mismatch",
            algorithm: "sha256:canonical-json",
            subject: "recorded_result_payload",
            recorded_digest: "res-digest",
            computed_digest: "other-digest",
          },
        }),
      }),
    );
    render(<RunActionsPanel runId="run-1" />);
    await openEvidence();
    expect(await screen.findByTestId("evidence-result-verification")).toHaveTextContent("摘要不匹配");
  });

  it("presents a missing result distinctly from an unknown one", async () => {
    taskApiMock.getActionEvidence.mockResolvedValue(
      evidenceResponse({ receipt: receipt({ result_state: "missing", outcome: null }) }),
    );
    render(<RunActionsPanel runId="run-1" />);
    await openEvidence();
    expect(await screen.findByTestId("evidence-result-state")).toHaveTextContent("未记录结果");
    expect(screen.queryByTestId("evidence-result-state")).not.toHaveTextContent("结果未知");
  });

  it("presents an unknown result distinctly", async () => {
    taskApiMock.getActionEvidence.mockResolvedValue(
      evidenceResponse({ receipt: receipt({ result_state: "unknown", outcome: "unknown" }) }),
    );
    render(<RunActionsPanel runId="run-1" />);
    await openEvidence();
    expect(await screen.findByTestId("evidence-result-state")).toHaveTextContent("结果未知");
  });

  it("presents an unsupported evidence query distinctly", async () => {
    taskApiMock.getActionEvidence.mockResolvedValue(
      evidenceResponse({ supported: false, ledger_source: "stub", receipt: undefined }),
    );
    render(<RunActionsPanel runId="run-1" />);
    await openEvidence();
    expect(await screen.findByTestId("evidence-unsupported")).toBeInTheDocument();
    expect(screen.queryByTestId("evidence-ready")).not.toBeInTheDocument();
  });

  it("presents a query failure distinctly and keeps the error code", async () => {
    taskApiMock.getActionEvidence.mockRejectedValue({
      detail: { error: { code: "TASK_CONTROL_ERROR", message: "action evidence query failed" } },
    });
    render(<RunActionsPanel runId="run-1" />);
    await openEvidence();
    expect(await screen.findByTestId("evidence-error")).toHaveTextContent("TASK_CONTROL_ERROR");
    expect(screen.getByTestId("evidence-error")).toHaveTextContent("action evidence query failed");
  });

  it("shows a linked decision reference and 未记录 when there is none", async () => {
    taskApiMock.getActionEvidence.mockResolvedValue(
      evidenceResponse({
        receipt: receipt({
          decision_ref: { status: "linked", decision_step_id: "agent-turn-000001", content_digest: "c-digest" },
        }),
      }),
    );
    const { unmount } = render(<RunActionsPanel runId="run-1" />);
    await openEvidence();
    expect(await screen.findByTestId("evidence-decision-ref")).toHaveTextContent("agent-turn-000001");
    unmount();

    taskApiMock.getActionEvidence.mockResolvedValue(evidenceResponse());
    render(<RunActionsPanel runId="run-1" />);
    await openEvidence();
    expect(await screen.findByTestId("evidence-decision-ref")).toHaveTextContent("未记录");
  });

  it("copies the stable reference", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    taskApiMock.getActionEvidence.mockResolvedValue(evidenceResponse());
    render(<RunActionsPanel runId="run-1" />);
    await openEvidence();
    await userEvent.click(screen.getByTestId("evidence-copy-ref"));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(
      `${window.location.origin}/ops-center/tasks/task-1?run_id=run-1&action_key=a-1`,
    ));
    expect(await screen.findByTestId("evidence-copied")).toBeInTheDocument();
  });

  it("re-locates the receipt by its stable identity", async () => {
    taskApiMock.getActionEvidence.mockResolvedValue(evidenceResponse());
    render(<RunActionsPanel runId="run-1" />);
    await openEvidence();
    await screen.findByTestId("evidence-ready");
    await userEvent.click(screen.getByTestId("evidence-relocate"));
    await waitFor(() => expect(taskApiMock.getActionEvidence).toHaveBeenCalledTimes(2));
    expect(taskApiMock.getActionEvidence).toHaveBeenLastCalledWith("run-1", "a-1");
    expect(screen.getByTestId("evidence-ready")).toBeInTheDocument();
  });

  it("drops a late receipt from a previously selected action", async () => {
    let resolveFirst!: (body: RunActionEvidenceResponse) => void;
    const firstResponse = new Promise<RunActionEvidenceResponse>((resolve) => { resolveFirst = resolve; });
    taskApiMock.getActionEvidence
      .mockReturnValueOnce(firstResponse)
      .mockResolvedValueOnce(evidenceResponse({ receipt: receipt({ action_key: "a-2", tool_name: "other_tool" }) }));
    render(<RunActionsPanel runId="run-1" />);
    await openEvidence(0);
    await openEvidence(1);
    expect(await screen.findByTestId("evidence-action-key")).toHaveTextContent("a-2");

    await act(async () => {
      resolveFirst(evidenceResponse({ receipt: receipt({ action_key: "a-1", tool_name: "stale_tool" }) }));
      await firstResponse;
    });
    expect(screen.getByTestId("evidence-action-key")).toHaveTextContent("a-2");
    expect(screen.queryByText("stale_tool")).not.toBeInTheDocument();
  });

  it("drops the receipt when the Run changes", async () => {
    taskApiMock.getActionEvidence.mockResolvedValue(evidenceResponse());
    const { rerender } = render(<RunActionsPanel runId="run-1" />);
    await openEvidence();
    expect(await screen.findByTestId("evidence-ready")).toBeInTheDocument();

    rerender(<RunActionsPanel runId="run-2" />);
    await waitFor(() => expect(screen.queryByTestId("evidence-panel")).not.toBeInTheDocument());
    expect(screen.queryByTestId("evidence-ready")).not.toBeInTheDocument();
  });

  it("opens a saved reference without relying on the action list order", async () => {
    const key = "act/键 with space & ?#%";
    window.history.replaceState({}, "", `/ops-center/tasks/task-1?${
      new URLSearchParams({ run_id: "run-1", action_key: key })
    }`);
    taskApiMock.getActionEvidence.mockResolvedValue(evidenceResponse({ receipt: receipt({ action_key: key }) }));
    render(<StrictMode><RunActionsPanel runId="run-1" /></StrictMode>);
    expect(await screen.findByTestId("evidence-action-key")).toHaveTextContent(key);
    expect(taskApiMock.getActionEvidence).toHaveBeenCalledWith("run-1", key);
    const link = screen.getByTestId("evidence-ref").querySelector("a")!;
    expect(new URL(link.href).searchParams.get("action_key")).toBe(key);
  });

  it("distinguishes an absent receipt from a query failure", async () => {
    taskApiMock.getActionEvidence.mockRejectedValue({ detail: { error: { code: "NOT_FOUND", message: "missing" } } });
    render(<RunActionsPanel runId="run-1" />);
    await openEvidence();
    expect(await screen.findByTestId("evidence-not-found")).toBeInTheDocument();
    expect(screen.queryByTestId("evidence-error")).not.toBeInTheDocument();
  });

  it("rejects a receipt for a different identity", async () => {
    taskApiMock.getActionEvidence.mockResolvedValue(evidenceResponse({ receipt: receipt({ action_key: "a-2" }) }));
    render(<RunActionsPanel runId="run-1" />);
    await openEvidence();
    expect(await screen.findByTestId("evidence-error")).toHaveTextContent("证据响应与所选动作不一致");
    expect(screen.queryByTestId("evidence-ready")).not.toBeInTheDocument();
  });

  it("drops late evidence during an A to B to A selection", async () => {
    let resolveOld!: (body: RunActionEvidenceResponse) => void;
    const old = new Promise<RunActionEvidenceResponse>((resolve) => { resolveOld = resolve; });
    taskApiMock.getActionEvidence.mockReturnValueOnce(old)
      .mockResolvedValueOnce(evidenceResponse({ receipt: receipt({ action_key: "a-2" }) }))
      .mockResolvedValueOnce(evidenceResponse({ receipt: receipt({ tool_name: "fresh" }) }));
    render(<RunActionsPanel runId="run-1" />);
    await openEvidence(0);
    await openEvidence(1);
    await screen.findByTestId("evidence-ready");
    await openEvidence(0);
    await waitFor(() => expect(screen.getByTestId("evidence-tool")).toHaveTextContent("fresh"));
    await act(async () => { resolveOld(evidenceResponse({ receipt: receipt({ tool_name: "stale" }) })); await old; });
    expect(screen.getByTestId("evidence-tool")).toHaveTextContent("fresh");
  });

  it("refreshes selected evidence and removes old content after revocation", async () => {
    taskApiMock.getActionEvidence.mockResolvedValueOnce(evidenceResponse()).mockRejectedValueOnce({
      detail: { error: { code: "FORBIDDEN", message: "revoked" } },
    });
    render(<RunActionsPanel runId="run-1" />);
    await openEvidence();
    await screen.findByTestId("evidence-ready");
    await userEvent.click(screen.getByRole("button", { name: "刷新" }));
    expect(await screen.findByTestId("evidence-error")).toHaveTextContent("FORBIDDEN");
    expect(screen.queryByTestId("evidence-ready")).not.toBeInTheDocument();
  });
});
