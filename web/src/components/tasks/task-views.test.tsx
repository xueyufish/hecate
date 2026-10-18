import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RunActionsPanel } from "./run-actions-panel";
import { TaskListTable } from "./task-list-table";
import type { RunActionItem, RunActionsResponse, TaskListResponse } from "@/lib/task-types";

const taskApiMock = vi.hoisted(() => ({
  list: vi.fn(),
  get: vi.fn(),
  getActions: vi.fn(),
}));

vi.mock("@/lib/task-types", async () => {
  const actual = await vi.importActual<typeof import("@/lib/task-types")>("@/lib/task-types");
  return { ...actual, taskApi: taskApiMock };
});

function actionsResponse(
  actions: Array<Partial<RunActionItem>>,
  overrides: Record<string, unknown> = {},
): RunActionsResponse {
  return {
    run_id: "run-1",
    task_id: "task-1",
    actions: actions.map((action, index) => ({
      action_key: `action-${index}`,
      execution_id: null,
      tool_call_id: null,
      tool_name: "biz_tool",
      arguments_digest: "d",
      side_effect_class: "non_idempotent_write",
      state: "claimed",
      outcome: null,
      result_digest: null,
      pending_reconciliation: false,
      ...action,
    })),
    ledger_source: "core",
    supported: true,
    ...overrides,
  };
}

beforeEach(() => {
  taskApiMock.list.mockReset();
  taskApiMock.get.mockReset();
  taskApiMock.getActions.mockReset();
});

describe("TaskListTable", () => {
  it("renders goal, lifecycle and created time per row", () => {
    const body: TaskListResponse = {
      items: [
        { task_id: "t-1", goal: "summarize inventory", lifecycle_state: "succeeded", created_at: "2026-10-18T00:00:00Z" },
        { task_id: "t-2", goal: "unrecorded task", lifecycle_state: "unrecorded", created_at: null },
      ],
      total: 2,
    };
    render(<TaskListTable tasks={body.items} />);
    expect(screen.getByText("summarize inventory")).toBeInTheDocument();
    expect(screen.getByText("已成功")).toBeInTheDocument();
    // unrecorded is explicitly labeled (lifecycle AND missing created time) —
    // never inferred as completed. Multiple 未记录 labels are expected.
    expect(screen.getAllByText("未记录").length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByTestId("task-row")).toHaveLength(2);
  });
});

describe("RunActionsPanel", () => {
  it("shows the unsupported banner for supported=false (never 暂无动作)", async () => {
    taskApiMock.getActions.mockResolvedValue(
      actionsResponse([], { supported: false, ledger_source: "stub" }),
    );
    render(<RunActionsPanel runId="run-1" />);
    await waitFor(() => expect(screen.getByTestId("actions-unsupported")).toBeInTheDocument());
    expect(screen.getByText(/不支持动作查询/)).toBeInTheDocument();
    expect(screen.queryByTestId("actions-empty")).not.toBeInTheDocument();
  });

  it("shows 暂无动作 for a real empty ledger", async () => {
    taskApiMock.getActions.mockResolvedValue(actionsResponse([]));
    render(<RunActionsPanel runId="run-1" />);
    await waitFor(() => expect(screen.getByTestId("actions-empty")).toBeInTheDocument());
  });

  it("renders claimed + succeeded as separate facts, not 执行中", async () => {
    taskApiMock.getActions.mockResolvedValue(
      actionsResponse([
        {
          action_key: "a-1",
          execution_id: "exec-1",
          tool_call_id: "tc-1",
          tool_name: "biz_tool",
          arguments_digest: "d",
          side_effect_class: "non_idempotent_write",
          state: "claimed",
          outcome: "succeeded",
          result_digest: "rd",
          pending_reconciliation: false,
        },
      ]),
    );
    render(<RunActionsPanel runId="run-1" />);
    await waitFor(() => expect(screen.getByTestId("actions-ready")).toBeInTheDocument());
    expect(screen.getByText("已领取")).toBeInTheDocument();
    expect(screen.getByText("已成功")).toBeInTheDocument();
    const body = document.body.textContent ?? "";
    expect(body).not.toContain("执行中");
  });

  it("shows pending-reconciliation flag and 未记录 outcome when absent", async () => {
    taskApiMock.getActions.mockResolvedValue(
      actionsResponse([
        {
          action_key: "a-2",
          execution_id: "exec-2",
          tool_call_id: "tc-2",
          tool_name: "biz_tool",
          arguments_digest: "d",
          side_effect_class: "external_side_effect",
          state: "outcome_unknown",
          outcome: null,
          result_digest: null,
          pending_reconciliation: true,
        },
      ]),
    );
    render(<RunActionsPanel runId="run-1" />);
    await waitFor(() => expect(screen.getByTestId("pending-flag")).toBeInTheDocument());
    expect(screen.getByText("结果未知")).toBeInTheDocument();
    const pendingFlag = screen.getByTestId("pending-flag");
    expect(pendingFlag).toHaveTextContent("待对账");  // row flag, not the column header
    // The absent outcome is explicitly 未记录, never inferred as succeeded.
    expect(within(pendingFlag.closest("tr") as HTMLElement).getByText("未记录")).toBeInTheDocument();
  });

  it("consumes the detail.error envelope on failure and offers refresh", async () => {
    taskApiMock.getActions.mockRejectedValue({
      detail: { error: { code: "TASK_CONTROL_ERROR", message: "action ledger query failed" } },
    });
    render(<RunActionsPanel runId="run-1" />);
    await waitFor(() => expect(screen.getByTestId("actions-error")).toBeInTheDocument());
    expect(screen.getByText(/action ledger query failed/)).toBeInTheDocument();
    expect(screen.queryByTestId("actions-empty")).not.toBeInTheDocument();
  });

  it("drops a stale response from a previous run and renders the selected run", async () => {
    let resolveA: (v: RunActionsResponse) => void = () => {};
    const slowA = new Promise<RunActionsResponse>((resolve) => {
      resolveA = resolve;
    });
    taskApiMock.getActions.mockImplementation((runId: string) => {
      if (runId === "run-A") return slowA;
      return Promise.resolve(
        actionsResponse([{ action_key: "b-key" }]),
      );
    });

    const { rerender } = render(<RunActionsPanel runId="run-A" />);
    await waitFor(() => expect(taskApiMock.getActions).toHaveBeenCalledWith("run-A"));

    // Switch to run-B before run-A's (slow) response arrives.
    rerender(<RunActionsPanel runId="run-B" />);
    await waitFor(() => expect(screen.getByTestId("actions-ready")).toBeInTheDocument());
    expect(screen.getByText("b-key")).toBeInTheDocument();

    // run-A's late response must be dropped, never rendered over run-B.
    resolveA(actionsResponse([{ action_key: "a-key" }]));
    await waitFor(() => expect(taskApiMock.getActions).toHaveBeenCalledTimes(2));
    expect(screen.queryByText("a-key")).not.toBeInTheDocument();
    expect(screen.getByText("b-key")).toBeInTheDocument();
  });

  it("refresh re-fetches the current run from the ready state", async () => {
    taskApiMock.getActions.mockResolvedValue(
      actionsResponse([{ action_key: "a-1" }]),
    );
    render(<RunActionsPanel runId="run-1" />);
    await waitFor(() => expect(screen.getByTestId("actions-ready")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "刷新" }));
    await waitFor(() => expect(taskApiMock.getActions).toHaveBeenCalledTimes(2));
    expect(screen.getByTestId("actions-ready")).toBeInTheDocument();
  });
});


describe("action request lifetime and refresh", () => {
  it.each([
    ["empty", actionsResponse([])],
    ["unsupported", actionsResponse([], { supported: false })],
  ])("can refresh the %s state to see newly available actions", async (_kind, initial) => {
    taskApiMock.getActions
      .mockResolvedValueOnce(initial)
      .mockResolvedValueOnce(actionsResponse([{ action_key: "new-action" }]));
    render(<RunActionsPanel runId="run-1" />);
    await screen.findByTestId(`actions-${_kind}`);
    await userEvent.click(screen.getByRole("button", { name: /刷新/ }));
    expect(await screen.findByText("new-action")).toBeInTheDocument();
  });

  it("rejects an earlier request even after switching back to the same Run", async () => {
    let resolveOld!: (body: RunActionsResponse) => void;
    const oldResponse = new Promise<RunActionsResponse>((resolve) => { resolveOld = resolve; });
    taskApiMock.getActions
      .mockReturnValueOnce(oldResponse)
      .mockResolvedValueOnce(actionsResponse([{ action_key: "b-action" }]))
      .mockResolvedValueOnce(actionsResponse([{ action_key: "new-a-action" }]));
    const { rerender } = render(<RunActionsPanel runId="run-A" />);
    rerender(<RunActionsPanel runId="run-B" />);
    await screen.findByText("b-action");
    rerender(<RunActionsPanel runId="run-A" />);
    await screen.findByText("new-a-action");
    await act(async () => {
      resolveOld(actionsResponse([{ action_key: "stale-a-action" }]));
      await oldResponse;
    });
    expect(screen.queryByText("stale-a-action")).not.toBeInTheDocument();
    expect(screen.getByText("new-a-action")).toBeInTheDocument();
  });

  it("displays the API error code as well as the message", async () => {
    taskApiMock.getActions.mockRejectedValue({
      detail: { error: { code: "NOT_FOUND", message: "run not found" } },
    });
    render(<RunActionsPanel runId="run-1" />);
    expect(await screen.findByTestId("actions-error")).toHaveTextContent("NOT_FOUND");
    expect(screen.getByTestId("actions-error")).toHaveTextContent("run not found");
  });
});
