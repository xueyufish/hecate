// Task detail page × controlled continuation: the trail is a task-level
// summary rendered outside the Run switcher — switching runs must never
// present it as the selected run's history, and the page's existing
// isolation (stale-while-revalidate per Task, refresh failure keeps the
// last ready detail) carries the trail with it.

import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
const routeState = vi.hoisted(() => ({ id: "task-1" }));
vi.mock("@/lib/api-client", () => ({ api: apiMock }));
vi.mock("next/navigation", () => ({ useParams: () => ({ id: routeState.id }) }));
vi.mock("next/link", () => ({
  default: ({ children }: { children: ReactNode }) => <a>{children}</a>,
}));

import TaskDetailPage from "../app/(dashboard)/ops-center/tasks/[id]/page";
import { lifecycleLabel, type TaskContinuationSummary, type TaskDetail } from "@/lib/task-types";

function detailOf(taskId: string, runs: TaskDetail["runs"], continuation?: TaskContinuationSummary) {
  return {
    task_id: taskId,
    goal: "核对库存并写回",
    issuer_domain: "hecate",
    workspace_id: "ws-1",
    lifecycle_state: "succeeded",
    revision: 3,
    created_at: "2026-10-27T00:00:00Z",
    runs,
    ...(continuation ? { continuation } : {}),
  } satisfies TaskDetail;
}

const run1 = {
  run_id: "run-1",
  attempt_no: 1,
  deployment_id: "dep-1",
  backend_ref: {},
  projection: { state: "failed", result_preview: '{"answer": "nothing relevant"}' },
  delivery_acceptance: {
    status: "acceptance_failed" as const,
    checker_version: "delivery-acceptance/1",
    checks: [{ kind: "content_contains", status: "failed", detail: "delivered content does not contain 'three items'" }],
  },
  event_cursor: 2,
};

const run2 = {
  run_id: "run-2",
  attempt_no: 2,
  deployment_id: "dep-1",
  backend_ref: {},
  projection: { state: "succeeded", result_preview: '{"answer": "three items in stock"}' },
  delivery_acceptance: {
    status: "passed" as const,
    checker_version: "delivery-acceptance/1",
    checks: [{ kind: "content_contains", status: "passed" }],
  },
  event_cursor: 5,
};

const TRAIL: TaskContinuationSummary = {
  max_continuations: 1,
  attempts: [
    {
      attempt: 1,
      candidate_decision_step_id: "agent-turn-000002",
      candidate_digest: "f".repeat(64),
      status: "acceptance_failed",
      standard_version: 2,
      effective_turn: 3,
      failed_checks: [
        { kind: "content_contains", status: "failed", detail: "delivered content does not contain 'three items'" },
      ],
    },
  ],
};

function panelResponse(path: string) {
  if (path.includes("/commands")) return Promise.resolve({ items: [], total: 0 });
  if (path.startsWith("/api/runs/") && path.endsWith("/actions")) {
    return Promise.resolve({
      run_id: path.split("/")[3],
      task_id: routeState.id,
      actions: [], ledger_source: "core", supported: true,
    });
  }
  if (path.startsWith("/api/runs/") && path.includes("/events")) {
    return Promise.resolve({ events: [], next_cursor: null, has_more: false });
  }
  return Promise.reject(new Error(`unexpected GET ${path}`));
}

beforeEach(() => {
  routeState.id = "task-1";
  apiMock.get.mockReset();
  apiMock.post.mockReset();
  // Sibling panels get real response shapes so their errors cannot mask
  // continuation failures in these page-level tests.
  apiMock.get.mockImplementation(panelResponse);
});

describe("TaskDetailPage continuation section", () => {
  it("shows the task-level trail and keeps it stable across run switching", async () => {
    apiMock.get.mockImplementation((path: string) =>
      path === "/api/tasks/task-1" ? Promise.resolve(detailOf("task-1", [run1, run2], TRAIL)) : panelResponse(path),
    );
    render(<TaskDetailPage />);

    const section = await screen.findByTestId("task-continuation-section");
    expect(within(section).getByTestId("continuation-triggered-count").textContent).toContain(
      "已记录触发次数：1",
    );
    expect(within(section).getByText("候选决策：agent-turn-000002")).toBeInTheDocument();
    expect(within(screen.getByTestId("run-delivery-section")).getByText("验收通过")).toBeInTheDocument();

    // Switching runs re-renders the run-scoped delivery verdict, while the
    // task-level trail stays exactly as recorded.
    await userEvent.click(screen.getByRole("button", { name: "Run #1" }));
    const delivery = screen.getByTestId("run-delivery-section");
    expect(within(delivery).getByText("验收未通过")).toBeInTheDocument();
    expect(within(section).getByTestId("continuation-triggered-count").textContent).toContain(
      "已记录触发次数：1",
    );
    expect(within(section).getByText("候选决策：agent-turn-000002")).toBeInTheDocument();
    expect(apiMock.post).not.toHaveBeenCalled();
  });

  it("reports no recorded continuation information when the summary is absent", async () => {
    apiMock.get.mockImplementation((path: string) =>
      path === "/api/tasks/task-1" ? Promise.resolve(detailOf("task-1", [run1])) : panelResponse(path),
    );
    render(<TaskDetailPage />);
    const section = await screen.findByTestId("task-continuation-section");
    expect(within(section).getByTestId("continuation-absent").textContent).toContain(
      "无已记录的续跑信息",
    );
    expect(within(section).getByTestId("continuation-panel").textContent).toContain(
      "不能据此推断续跑策略已关闭",
    );
  });

  it("keeps the last trail when a refresh fails", async () => {
    let detailCalls = 0;
    apiMock.get.mockImplementation((path: string) => {
      if (path !== "/api/tasks/task-1") return panelResponse(path);
      detailCalls += 1;
      return detailCalls === 1
        ? Promise.resolve(detailOf("task-1", [run2], TRAIL))
        : Promise.reject(new Error("network down"));
    });
    render(<TaskDetailPage />);
    expect(await screen.findByText("候选决策：agent-turn-000002")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "刷新任务" }));
    expect(await screen.findByTestId("task-refresh-error")).toBeInTheDocument();
    // The previously read trail stays on screen under the banner.
    expect(screen.getByText("候选决策：agent-turn-000002")).toBeInTheDocument();
  });

  it.each(["succeeded", "failed", "cancelled"])("keeps the task lifecycle %s separate from trigger and verdict facts", async (state) => {
    apiMock.get.mockImplementation((path: string) => path === "/api/tasks/task-1"
      ? Promise.resolve({ ...detailOf("task-1", [run1], TRAIL), lifecycle_state: state })
      : panelResponse(path));
    render(<TaskDetailPage />);
    const section = await screen.findByTestId("task-continuation-section");
    expect(within(screen.getByTestId("task-summary")).getByText(lifecycleLabel(state))).toBeInTheDocument();
    expect(within(section).getByTestId("continuation-bound-reached")).toHaveTextContent("任务状态见页面顶部");
    expect(within(screen.getByTestId("run-delivery-section")).getByText("验收未通过")).toBeInTheDocument();
    expect(apiMock.post).not.toHaveBeenCalled();
  });

  it.each(["waiting_input", "waiting_approval"])("keeps the trail visible during %s without issuing a command", async (state) => {
    const detail: TaskDetail = {
      ...detailOf("task-1", [], TRAIL),
      lifecycle_state: state,
      wait: {
        wake_kind: state === "waiting_input" ? "provide_input" : "resume",
        contract_ref: state === "waiting_input"
          ? { tool: "query_inventory", kind: "input", contract: { fields: ["region"] } }
          : { approval_ref: "task:t1:ref1", tool_name: "create_ticket", risk_level: "high", target_resources: ["tickets/42"], invocation_id: "inv-1" },
        wait_token: "tok-1", wait_expires_at: null, consumed: false,
      },
    };
    apiMock.get.mockImplementation((path: string) => {
      if (path === "/api/tasks/task-1") return Promise.resolve(detail);
      if (path === "/api/tasks/task-1/approval") return Promise.resolve({
        task_id: "task-1", workspace_id: "ws-1", revision: 3,
        wait: { consumed: false, expires_at: null, wait_token: "tok-1" },
        contract: detail.wait?.contract_ref,
        decision: null, can_decide: false, read_only_reason: "INSUFFICIENT_ROLE",
        authority: { judgment: "pending", decided_by: null, expires_at: null },
      });
      return panelResponse(path);
    });
    render(<TaskDetailPage />);
    const section = await screen.findByTestId("task-continuation-section");
    expect(within(section).getByTestId("continuation-triggered-count")).toHaveTextContent("已记录触发次数：1");
    expect(within(screen.getByTestId("task-summary")).getByText(lifecycleLabel(state))).toBeInTheDocument();
    if (state === "waiting_input") {
      expect(screen.getByTestId("task-input-wait-summary")).toHaveTextContent("等待人工输入");
    } else {
      expect(await screen.findByTestId("task-approval-wait-summary")).toHaveTextContent("create_ticket");
    }
    expect(apiMock.post).not.toHaveBeenCalled();
  });

  it("replaces the old trail on a successful refresh", async () => {
    let current = detailOf("task-1", [run2], TRAIL);
    apiMock.get.mockImplementation((path: string) => path === "/api/tasks/task-1"
      ? Promise.resolve(current) : panelResponse(path));
    render(<TaskDetailPage />);
    expect(await screen.findByText("候选决策：agent-turn-000002")).toBeInTheDocument();
    current = detailOf("task-1", [run2]);
    await userEvent.click(screen.getByRole("button", { name: "刷新任务" }));
    expect(await screen.findByTestId("continuation-absent")).toBeInTheDocument();
    expect(screen.queryByText("候选决策：agent-turn-000002")).not.toBeInTheDocument();
    expect(apiMock.post).not.toHaveBeenCalled();
  });

  it("ignores an old Task response carrying a trail after navigation", async () => {
    let resolveOld: (detail: ReturnType<typeof detailOf>) => void = () => {};
    const oldResponse = new Promise<ReturnType<typeof detailOf>>((resolve) => { resolveOld = resolve; });
    const second = detailOf("task-2", [{ ...run1, run_id: "run-task-2", projection: { state: "failed", result_preview: "second task delivery" } }]);
    apiMock.get.mockImplementation((path: string) => {
      if (path === "/api/tasks/task-1") return oldResponse;
      if (path === "/api/tasks/task-2") return Promise.resolve(second);
      return panelResponse(path);
    });
    const view = render(<TaskDetailPage />);
    await waitFor(() => expect(apiMock.get).toHaveBeenCalledWith("/api/tasks/task-1"));
    routeState.id = "task-2";
    view.rerender(<TaskDetailPage />);
    expect(await screen.findByText("second task delivery")).toBeInTheDocument();
    await act(async () => { resolveOld(detailOf("task-1", [run2], TRAIL)); });
    // The late task-1 trail never bleeds into the task-2 view.
    expect(screen.getByText("second task delivery")).toBeInTheDocument();
    expect(screen.queryByText("候选决策：agent-turn-000002")).not.toBeInTheDocument();
    expect(screen.queryByTestId("continuation-attempts")).not.toBeInTheDocument();
  });
});
