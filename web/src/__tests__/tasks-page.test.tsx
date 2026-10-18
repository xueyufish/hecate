import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import TasksPage from "@/app/(dashboard)/ops-center/tasks/page";
import TaskDetailPage from "@/app/(dashboard)/ops-center/tasks/[id]/page";
import type { RunEventsPage } from "@/lib/run-event-types";
import type { RunActionsResponse, RunSummary, TaskDetail, TaskListResponse } from "@/lib/task-types";

const apiMock = vi.hoisted(() => ({ get: vi.fn() }));
const route = vi.hoisted(() => ({ id: "task-1" }));
vi.mock("@/lib/api-client", () => ({ api: apiMock }));
vi.mock("next/navigation", () => ({ useParams: () => route }));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function run(runId: string, attempt: number): RunSummary {
  return {
    run_id: runId, attempt_no: attempt, deployment_id: "deployment-1",
    backend_ref: {}, projection: {}, event_cursor: 0,
  };
}

function detail(taskId = "task-1", runs = [run("run-1", 1), run("run-2", 2)]): TaskDetail {
  return {
    task_id: taskId, goal: `Full goal for ${taskId}`, issuer_domain: "platform",
    workspace_id: "workspace-1", lifecycle_state: "unrecorded", created_at: null, runs,
  };
}

function actions(runId: string, actionKey?: string): RunActionsResponse {
  return {
    run_id: runId, task_id: "task-1", supported: true, ledger_source: "core",
    actions: actionKey ? [{
      action_key: actionKey, tool_name: "biz_tool", execution_id: null, tool_call_id: null,
      arguments_digest: null, side_effect_class: null, state: "claimed", outcome: "succeeded",
      result_digest: null, pending_reconciliation: false,
    }] : [],
  };
}

function taskList(goal: string, total = 21): TaskListResponse {
  return {
    items: [{ task_id: "task-1", goal, lifecycle_state: "unrecorded", created_at: null }], total,
  };
}

function emptyEvents(): RunEventsPage {
  return { events: [], next_cursor: null, has_more: false };
}

beforeEach(() => {
  apiMock.get.mockReset();
  route.id = "task-1";
});

describe("task list page", () => {
  it("requests real pagination URLs and links to the selected task", async () => {
    apiMock.get.mockResolvedValueOnce(taskList("page one")).mockResolvedValueOnce(taskList("page two"));
    render(<TasksPage />);
    const link = await screen.findByRole("link", { name: "page one" });
    expect(link).toHaveAttribute("href", "/ops-center/tasks/task-1");
    expect(apiMock.get).toHaveBeenCalledWith("/api/tasks?page=1&page_size=20");
    expect(screen.getByRole("button", { name: "上一页" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "下一页" }));
    expect(await screen.findByRole("link", { name: "page two" })).toBeInTheDocument();
    expect(apiMock.get).toHaveBeenCalledWith("/api/tasks?page=2&page_size=20");
    expect(screen.getByRole("button", { name: "下一页" })).toBeDisabled();
  });

  it("keeps navigation available when a later page becomes empty", async () => {
    apiMock.get
      .mockResolvedValueOnce(taskList("first page"))
      .mockResolvedValueOnce({ items: [], total: 20 })
      .mockResolvedValueOnce(taskList("back on first page", 20));
    render(<TasksPage />);
    await screen.findByText("first page");
    await userEvent.click(screen.getByRole("button", { name: "下一页" }));
    await screen.findByTestId("tasks-empty");
    await userEvent.click(screen.getByRole("button", { name: "上一页" }));
    expect(await screen.findByText("back on first page")).toBeInTheDocument();
  });

  it("can refresh an empty list", async () => {
    apiMock.get.mockResolvedValueOnce({ items: [], total: 0 }).mockResolvedValueOnce(taskList("new task", 1));
    render(<TasksPage />);
    await screen.findByTestId("tasks-empty");
    await userEvent.click(screen.getByRole("button", { name: /刷新/ }));
    expect(await screen.findByText("new task")).toBeInTheDocument();
  });

  it("shows error code and message and retries without reporting an empty list", async () => {
    apiMock.get
      .mockRejectedValueOnce({ detail: { error: { code: "FORBIDDEN", message: "Workspace required" } } })
      .mockResolvedValueOnce(taskList("recovered", 1));
    render(<TasksPage />);
    expect(await screen.findByTestId("tasks-error")).toHaveTextContent("FORBIDDEN");
    expect(screen.getByTestId("tasks-error")).toHaveTextContent("Workspace required");
    expect(screen.queryByTestId("tasks-empty")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("recovered")).toBeInTheDocument();
  });
});

describe("task detail page", () => {
  it("selects the latest attempt, switches Run and refreshes the task", async () => {
    apiMock.get.mockImplementation((path: string) => {
      if (path === "/api/tasks/task-1") return Promise.resolve(detail());
      if (path === "/api/runs/run-1/actions") return Promise.resolve(actions("run-1", "older-action"));
      if (path === "/api/runs/run-2/actions") return Promise.resolve(actions("run-2", "latest-action"));
      if (path.startsWith("/api/runs/") && path.endsWith("/events")) return Promise.resolve(emptyEvents());
      throw new Error(`Unexpected request: ${path}`);
    });
    render(<TaskDetailPage />);
    await screen.findByText("latest-action");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Full goal for task-1");
    expect(within(screen.getByTestId("task-summary")).getAllByText(/未记录/).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Run #2" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: "Run #1" }));
    expect(await screen.findByText("older-action")).toBeInTheDocument();
    expect(screen.queryByText("latest-action")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "刷新任务" }));
    await screen.findByText("older-action");
    expect(apiMock.get.mock.calls.filter(([path]) => path === "/api/tasks/task-1")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Run #1" })).toHaveAttribute("aria-pressed", "true");
  });

  it("shows no Run without requesting actions, then discovers a new Run on refresh", async () => {
    let taskRequests = 0;
    apiMock.get.mockImplementation((path: string) => {
      if (path === "/api/tasks/task-1") {
        taskRequests += 1;
        return Promise.resolve(detail("task-1", taskRequests === 1 ? [] : [run("run-1", 1)]));
      }
      if (path === "/api/runs/run-1/actions") return Promise.resolve(actions("run-1", "first-action"));
      if (path === "/api/runs/run-1/events") return Promise.resolve(emptyEvents());
      throw new Error(`Unexpected request: ${path}`);
    });
    render(<TaskDetailPage />);
    await screen.findByTestId("runs-empty");
    expect(apiMock.get).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("actions-no-run")).toBeInTheDocument();
    expect(screen.getByTestId("events-no-run")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "刷新任务" }));
    expect(await screen.findByText("first-action")).toBeInTheDocument();
    expect(await screen.findByTestId("run-events-empty")).toBeInTheDocument();
    expect(apiMock.get).toHaveBeenCalledWith("/api/runs/run-1/events");
  });

  it("rejects a previous task response after route navigation", async () => {
    const old = deferred<TaskDetail>();
    apiMock.get.mockImplementation((path: string) => {
      if (path === "/api/tasks/task-1") return old.promise;
      if (path === "/api/tasks/task-2") return Promise.resolve(detail("task-2", []));
      throw new Error(`Unexpected request: ${path}`);
    });
    const { rerender } = render(<TaskDetailPage />);
    route.id = "task-2";
    rerender(<TaskDetailPage />);
    await screen.findByText("Full goal for task-2");
    await act(async () => { old.resolve(detail("task-1", [])); await old.promise; });
    expect(screen.queryByText("Full goal for task-1")).not.toBeInTheDocument();
    expect(screen.getByText("Full goal for task-2")).toBeInTheDocument();
  });

  it("rejects a late error from a previous task", async () => {
    const old = deferred<TaskDetail>();
    apiMock.get.mockImplementation((path: string) =>
      path === "/api/tasks/task-1" ? old.promise : Promise.resolve(detail("task-2", [])));
    const { rerender } = render(<TaskDetailPage />);
    route.id = "task-2";
    rerender(<TaskDetailPage />);
    await screen.findByText("Full goal for task-2");
    await act(async () => { old.reject(new Error("stale failure")); await old.promise.catch(() => {}); });
    expect(screen.queryByTestId("task-error")).not.toBeInTheDocument();
    expect(screen.getByText("Full goal for task-2")).toBeInTheDocument();
  });

  it("shows a missing task error code and offers retry and list navigation", async () => {
    apiMock.get.mockRejectedValueOnce({ detail: { error: { code: "NOT_FOUND", message: "Task not found" } } })
      .mockResolvedValueOnce(detail("task-1", []));
    render(<TaskDetailPage />);
    expect(await screen.findByTestId("task-error")).toHaveTextContent("NOT_FOUND");
    expect(screen.getByRole("link", { name: /返回任务列表/ })).toHaveAttribute("href", "/ops-center/tasks");
    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    await waitFor(() => expect(screen.getByTestId("runs-empty")).toBeInTheDocument());
  });

  it("keeps the actions panel working when the events endpoint fails", async () => {
    apiMock.get.mockImplementation((path: string) => {
      if (path === "/api/tasks/task-1") return Promise.resolve(detail("task-1", [run("run-1", 1)]));
      if (path === "/api/runs/run-1/actions") return Promise.resolve(actions("run-1", "isolated-action"));
      if (path.startsWith("/api/runs/") && path.endsWith("/events")) {
        return Promise.reject({ detail: { error: { code: "EVENTS_DOWN", message: "events unavailable" } } });
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    render(<TaskDetailPage />);
    expect(await screen.findByTestId("run-events-error")).toHaveTextContent("EVENTS_DOWN");
    expect(await screen.findByText("isolated-action")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试" })).toBeEnabled();
  });
});
