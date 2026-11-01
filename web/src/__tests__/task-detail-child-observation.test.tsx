// Task detail page × parent-child delegation observation: the section is a
// task-level read-only view rendered outside the Run switcher. Switching
// runs, refreshing (including failing refreshes) and navigating between
// tasks must never invent relationships, never issue a write, and never
// present a relationship reference as an access grant.

import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
const routeState = vi.hoisted(() => ({ id: "task-1" }));
vi.mock("@/lib/api-client", () => ({ api: apiMock }));
vi.mock("next/navigation", () => ({ useParams: () => ({ id: routeState.id }) }));
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: { children: ReactNode; href: string } & Record<string, unknown>) => (
    <a href={href} {...rest}>{children}</a>
  ),
}));

import TaskDetailPage from "../app/(dashboard)/ops-center/tasks/[id]/page";
import { type AgentChildrenSummary, type TaskDetail } from "@/lib/task-types";

const CHILD_ID = "22222222-2222-4222-8222-222222222222";
const PARENT_ID = "11111111-1111-4111-8111-111111111111";
const LOCAL_CHILD_REF = { kind: "task", issuer_domain: "hecate", id: CHILD_ID };
const LOCAL_PARENT_REF = { kind: "task", issuer_domain: "hecate", id: PARENT_ID };

function detailOf(taskId: string, overrides: Partial<TaskDetail> = {}): TaskDetail {
  return {
    task_id: taskId,
    goal: "核对库存并写回",
    issuer_domain: "hecate",
    workspace_id: "ws-1",
    lifecycle_state: "succeeded",
    revision: 3,
    created_at: "2026-10-27T00:00:00Z",
    runs: [
      {
        run_id: `run-${taskId}`,
        attempt_no: 1,
        deployment_id: "dep-1",
        backend_ref: {},
        projection: { state: "succeeded", result_preview: "delivered" },
        event_cursor: 1,
      },
    ],
    ...overrides,
  };
}

function childrenOf(overrides: Partial<AgentChildrenSummary> = {}): AgentChildrenSummary {
  return {
    accepted_calls: 1,
    pending: { alias: "worker", call_id: "call-1", child_task_ref: LOCAL_CHILD_REF, consumed: false },
    ...overrides,
  };
}

function panelResponse(path: string) {
  if (path.includes("/commands")) return Promise.resolve({ items: [], total: 0 });
  if (path.startsWith("/api/runs/") && path.endsWith("/actions")) {
    return Promise.resolve({
      run_id: path.split("/")[3],
      task_id: routeState.id,
      actions: [],
      ledger_source: "core",
      supported: true,
    });
  }
  if (path.startsWith("/api/runs/") && path.includes("/events")) {
    return Promise.resolve({ events: [], next_cursor: null, has_more: false });
  }
  return Promise.reject(new Error(`unexpected GET ${path}`));
}

function detailRoute(taskId: string, detail: TaskDetail) {
  return (path: string) =>
    path === `/api/tasks/${taskId}` ? Promise.resolve(detail) : panelResponse(path);
}

beforeEach(() => {
  routeState.id = "task-1";
  apiMock.get.mockReset();
  apiMock.post.mockReset();
  apiMock.get.mockImplementation(panelResponse);
});

describe("TaskDetailPage parent-child observation section", () => {
  it("surfaces parent-side facts and navigates to the child detail via the validated link", async () => {
    apiMock.get.mockImplementation(detailRoute("task-1", detailOf("task-1", { agent_children: childrenOf() })));
    render(<TaskDetailPage />);
    const section = await screen.findByTestId("task-child-observation-section");
    const view = within(section).getByTestId("agent-children-view");
    expect(within(view).getByTestId("agent-children-accepted-calls").textContent).toContain("已接受调用次数：1");
    expect(within(view).getByTestId("agent-children-consumed-state").textContent).toContain("等待回调消费");
    const link = within(section).getByTestId("agent-children-child-link");
    expect(link.getAttribute("href")).toBe(`/ops-center/tasks/${CHILD_ID}`);
    expect(apiMock.post).not.toHaveBeenCalled();
  });

  it("surfaces child-side facts and the back-to-parent navigation", async () => {
    apiMock.get.mockImplementation(
      detailRoute("task-1", {
        ...detailOf("task-1"),
        agent_child_parent: { alias: "worker", call_id: "call-1", parent_task_ref: LOCAL_PARENT_REF },
      }),
    );
    render(<TaskDetailPage />);
    const section = await screen.findByTestId("task-child-observation-section");
    const view = within(section).getByTestId("agent-child-parent-view");
    expect(view.textContent).toContain("调用别名：worker");
    const link = within(section).getByTestId("agent-child-parent-link");
    expect(link.getAttribute("href")).toBe(`/ops-center/tasks/${PARENT_ID}`);
    expect(apiMock.post).not.toHaveBeenCalled();
  });

  it("shows no recorded relationship without implying delegation is disabled", async () => {
    apiMock.get.mockImplementation(detailRoute("task-1", detailOf("task-1")));
    render(<TaskDetailPage />);
    const section = await screen.findByTestId("task-child-observation-section");
    expect(within(section).getByTestId("agent-observation-absent").textContent).toContain(
      "无已记录的父子调用信息",
    );
    expect(within(section).getByText(/不能据此推断委托能力已关闭/)).toBeInTheDocument();
    expect(apiMock.post).not.toHaveBeenCalled();
  });

  it("keeps the section stable across run switching, with run-scoped verdicts separate", async () => {
    const detail = detailOf("task-1", {
      runs: [
        {
          run_id: "run-a",
          attempt_no: 1,
          deployment_id: "dep-1",
          backend_ref: {},
          projection: { state: "succeeded", result_preview: "first" },
          event_cursor: 1,
        },
        {
          run_id: "run-b",
          attempt_no: 2,
          deployment_id: "dep-1",
          backend_ref: {},
          projection: { state: "failed", result_preview: "second" },
          event_cursor: 2,
        },
      ],
      agent_children: childrenOf(),
    });
    apiMock.get.mockImplementation(detailRoute("task-1", detail));
    render(<TaskDetailPage />);
    const section = await screen.findByTestId("task-child-observation-section");
    expect(within(section).getByTestId("agent-children-accepted-calls").textContent).toContain("已接受调用次数：1");
    expect(screen.getByRole("button", { name: "Run #2" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: "Run #1" }));
    expect(screen.getByRole("button", { name: "Run #1" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Run #2" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByTestId("run-delivery-section")).toHaveTextContent("first");
    expect(within(section).getByTestId("agent-children-accepted-calls").textContent).toContain("已接受调用次数：1");
    expect(within(section).getByTestId("agent-children-child-link").getAttribute("href")).toBe(
      `/ops-center/tasks/${CHILD_ID}`,
    );
    expect(apiMock.post).not.toHaveBeenCalled();
  });

  it("keeps the last recorded relationship when a refresh fails and marks the data stale", async () => {
    let detailCalls = 0;
    apiMock.get.mockImplementation((path: string) => {
      if (path !== "/api/tasks/task-1") return panelResponse(path);
      detailCalls += 1;
      return detailCalls === 1
        ? Promise.resolve(detailOf("task-1", { agent_children: childrenOf() }))
        : Promise.reject(new Error("network down"));
    });
    render(<TaskDetailPage />);
    expect(await screen.findByTestId("agent-children-child-link")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "刷新任务" }));
    expect(await screen.findByTestId("task-refresh-error")).toBeInTheDocument();
    // The stale view keeps the recorded facts on screen under the banner.
    expect(screen.getByTestId("agent-children-child-link").getAttribute("href")).toBe(
      `/ops-center/tasks/${CHILD_ID}`,
    );
    expect(apiMock.post).not.toHaveBeenCalled();
  });

  it("replaces the relationship on a successful refresh instead of merging histories", async () => {
    let current = detailOf("task-1", { agent_children: childrenOf() });
    apiMock.get.mockImplementation((path: string) =>
      path === "/api/tasks/task-1" ? Promise.resolve(current) : panelResponse(path),
    );
    render(<TaskDetailPage />);
    expect(await screen.findByTestId("agent-children-child-link")).toBeInTheDocument();
    current = detailOf("task-1");
    await userEvent.click(screen.getByRole("button", { name: "刷新任务" }));
    expect(await screen.findByTestId("agent-observation-absent")).toBeInTheDocument();
    expect(screen.queryByTestId("agent-children-child-link")).not.toBeInTheDocument();
    expect(apiMock.post).not.toHaveBeenCalled();
  });

  it("isolates a late parent response after the user navigated to another task", async () => {
    let resolveOld: (detail: TaskDetail) => void = () => {};
    const oldResponse = new Promise<TaskDetail>((resolve) => {
      resolveOld = resolve;
    });
    const second = detailOf("task-2", {
      agent_child_parent: { alias: "worker", call_id: "call-9", parent_task_ref: LOCAL_PARENT_REF },
    });
    apiMock.get.mockImplementation((path: string) => {
      if (path === "/api/tasks/task-1") return oldResponse;
      if (path === "/api/tasks/task-2") return Promise.resolve(second);
      return panelResponse(path);
    });
    const view = render(<TaskDetailPage />);
    await waitFor(() => expect(apiMock.get).toHaveBeenCalledWith("/api/tasks/task-1"));
    routeState.id = "task-2";
    view.rerender(<TaskDetailPage />);
    expect(await screen.findByTestId("agent-child-parent-view")).toBeInTheDocument();
    await act(async () => {
      resolveOld(detailOf("task-1", { agent_children: childrenOf() }));
    });
    // The late task-1 parent facts never bleed into the task-2 child view.
    expect(screen.getByTestId("agent-child-parent-view")).toBeInTheDocument();
    expect(screen.queryByTestId("agent-children-view")).not.toBeInTheDocument();
    expect(apiMock.post).not.toHaveBeenCalled();
  });

  it("keeps the observation visible during waits without issuing any command", async () => {
    const detail: TaskDetail = {
      ...detailOf("task-1", { agent_children: childrenOf() }),
      lifecycle_state: "waiting_input",
      wait: {
        wake_kind: "agent_child",
        contract_ref: { alias: "worker", call_id: "call-1", child_task_ref: LOCAL_CHILD_REF, kind: "child" },
        wait_token: "tok-1",
        wait_expires_at: null,
        consumed: false,
      },
    };
    apiMock.get.mockImplementation(detailRoute("task-1", detail));
    render(<TaskDetailPage />);
    const section = await screen.findByTestId("task-child-observation-section");
    expect(within(section).getByTestId("agent-children-consumed-state").textContent).toContain("等待回调消费");
    expect(screen.getByTestId("task-summary").textContent).toContain("等待输入");
    expect(apiMock.post).not.toHaveBeenCalled();
  });

  it("queries the linked child independently and retries a denied navigation without old parent facts", async () => {
    let childReads = 0;
    apiMock.get.mockImplementation((path: string) => {
      if (path === "/api/tasks/task-1") {
        return Promise.resolve(detailOf("task-1", { agent_children: childrenOf() }));
      }
      if (path === `/api/tasks/${CHILD_ID}`) {
        childReads += 1;
        return childReads === 1
          ? Promise.reject({ detail: { error: { code: "not_found", message: "Task is not available" } } })
          : Promise.resolve(detailOf(CHILD_ID, {
            agent_child_parent: { alias: "worker", call_id: "call-1", parent_task_ref: LOCAL_PARENT_REF },
            runs: [],
          }));
      }
      return panelResponse(path);
    });
    const view = render(<TaskDetailPage />);
    expect(await screen.findByTestId("agent-children-child-link")).toHaveAttribute(
      "href", `/ops-center/tasks/${CHILD_ID}`,
    );
    routeState.id = CHILD_ID;
    view.rerender(<TaskDetailPage />);
    expect(await screen.findByTestId("task-error")).toHaveTextContent("not_found");
    expect(apiMock.get).toHaveBeenCalledWith(`/api/tasks/${CHILD_ID}`);
    expect(screen.queryByTestId("agent-children-view")).not.toBeInTheDocument();
    expect(screen.queryByTestId("agent-child-parent-view")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByTestId("agent-child-parent-link")).toHaveAttribute(
      "href", `/ops-center/tasks/${PARENT_ID}`,
    );
    expect(childReads).toBe(2);
    expect(apiMock.post).not.toHaveBeenCalled();
  });
});
