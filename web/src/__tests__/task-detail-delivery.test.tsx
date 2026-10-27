// Task detail page × delivery/acceptance: the Run switcher drives which
// run's delivery facts the panel shows; the page's existing isolation
// (stale-while-revalidate per Task, refresh failure keeps the last ready
// detail) carries the delivery view with it — this file pins the run-level
// switching and the no-run empty state for the delivery section.

import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

const apiMock = vi.hoisted(() => ({ get: vi.fn() }));
const routeState = vi.hoisted(() => ({ id: "task-1" }));
vi.mock("@/lib/api-client", () => ({ api: apiMock }));
vi.mock("next/navigation", () => ({ useParams: () => ({ id: routeState.id }) }));
vi.mock("next/link", () => ({
  default: ({ children }: { children: ReactNode }) => <a>{children}</a>,
}));

import TaskDetailPage from "../app/(dashboard)/ops-center/tasks/[id]/page";
import type { TaskDetail } from "@/lib/task-types";

function detailOf(runs: TaskDetail["runs"]) {
  return {
    task_id: "task-1",
    goal: "核对库存并写回",
    issuer_domain: "hecate",
    workspace_id: "ws-1",
    lifecycle_state: "failed",
    revision: 3,
    created_at: "2026-10-27T00:00:00Z",
    runs,
  };
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

beforeEach(() => {
  routeState.id = "task-1";
  apiMock.get.mockReset();
  // Route by path: sibling panels (actions, events) get their real shapes —
  // the delivery assertions must not depend on sibling-panel error states.
  apiMock.get.mockImplementation((path: string) => {
    if (path === "/api/tasks/task-1") {
      return Promise.reject(new Error("detail not stubbed for this test"));
    }
    if (path.startsWith("/api/runs/") && path.endsWith("/actions")) {
      return Promise.resolve({
        run_id: "run-x",
        task_id: "task-1",
        actions: [],
        ledger_source: "core",
        supported: true,
      });
    }
    return Promise.resolve({ events: [], next_cursor: null, has_more: false });
  });
});

describe("TaskDetailPage delivery section", () => {
  it("shows the latest run's delivery facts and switches with the Run selector", async () => {
    apiMock.get.mockImplementation((path: string) =>
      path === "/api/tasks/task-1" ? Promise.resolve(detailOf([run1, run2])) : Promise.reject(new Error(path)),
    );
    render(<TaskDetailPage />);

    const section = await screen.findByTestId("run-delivery-section");
    // Default selection is the latest attempt; its verdict shows first.
    expect(within(section).getByText("验收通过")).toBeInTheDocument();
    expect(within(section).getByText(/three items in stock/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Run #1" }));
    expect(within(section).getByText("验收未通过")).toBeInTheDocument();
    expect(
      within(section).getByText(/delivered content does not contain 'three items'/),
    ).toBeInTheDocument();
    // Attempt-level facts only — the other run's preview never bleeds in.
    expect(within(section).queryByText(/nothing relevant/)).toBeInTheDocument();
  });

  it("reports no delivery facts when the task has no runs", async () => {
    apiMock.get.mockImplementation((path: string) =>
      path === "/api/tasks/task-1" ? Promise.resolve(detailOf([])) : Promise.reject(new Error(path)),
    );
    render(<TaskDetailPage />);
    expect(await screen.findByTestId("delivery-no-run")).toBeInTheDocument();
  });

  it("keeps the last delivery view when a refresh fails", async () => {
    let detailCalls = 0;
    apiMock.get.mockImplementation((path: string) => {
      if (path !== "/api/tasks/task-1") return Promise.reject(new Error(path));
      detailCalls += 1;
      return detailCalls === 1 ? Promise.resolve(detailOf([run2])) : Promise.reject(new Error("network down"));
    });
    render(<TaskDetailPage />);
    expect(await screen.findByText("验收通过")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "刷新任务" }));
    expect(await screen.findByTestId("task-refresh-error")).toBeInTheDocument();
    // The previously read delivery facts stay on screen under the banner.
    expect(screen.getByText("验收通过")).toBeInTheDocument();
  });

  it("ignores an old Task response arriving after navigation", async () => {
    let resolveOld: (detail: ReturnType<typeof detailOf>) => void = () => {};
    const oldResponse = new Promise<ReturnType<typeof detailOf>>((resolve) => { resolveOld = resolve; });
    const second = {
      ...detailOf([{ ...run1, run_id: "run-task-2", projection: { state: "failed", result_preview: "second task delivery" } }]),
      task_id: "task-2",
    };
    apiMock.get.mockImplementation((path: string) => {
      if (path === "/api/tasks/task-1") return oldResponse;
      if (path === "/api/tasks/task-2") return Promise.resolve(second);
      return Promise.reject(new Error(path));
    });
    const view = render(<TaskDetailPage />);
    await waitFor(() => expect(apiMock.get).toHaveBeenCalledWith("/api/tasks/task-1"));
    routeState.id = "task-2";
    view.rerender(<TaskDetailPage />);
    expect(await screen.findByText("second task delivery")).toBeInTheDocument();
    await act(async () => { resolveOld(detailOf([run2])); });
    expect(screen.getByText("second task delivery")).toBeInTheDocument();
    expect(screen.queryByText("验收通过")).not.toBeInTheDocument();
    expect(screen.queryByText(/three items in stock/)).not.toBeInTheDocument();
  });
});
