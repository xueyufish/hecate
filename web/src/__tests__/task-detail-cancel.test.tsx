// Task detail page cancellation loop: the issued command refreshes both the
// Task detail and the command receipt list; the Task lifecycle badge changes
// only through the re-queried detail (never from the receipt itself); and a
// refresh failure keeps the received receipt on screen instead of reading as
// a failed submission.

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("@/lib/api-client", () => ({ api: apiMock }));
vi.mock("next/navigation", () => ({ useParams: () => ({ id: "task-1" }) }));
vi.mock("next/link", () => ({
  default: ({ children }: { children: ReactNode }) => <a>{children}</a>,
}));

import TaskDetailPage from "../app/(dashboard)/ops-center/tasks/[id]/page";
import type { CommandIssueResponse, TaskCommandItem, TaskCommandsPage } from "@/lib/task-command-types";

function detail(overrides: Record<string, unknown> = {}) {
  return {
    task_id: "task-1",
    goal: "核对库存并写回",
    issuer_domain: "platform",
    workspace_id: "ws-1",
    lifecycle_state: "running",
    revision: 2,
    created_at: "2026-10-24T00:00:00+00:00",
    runs: [],
    ...overrides,
  };
}

function commandRow(id: string, state: string): TaskCommandItem {
  return {
    command_id: id,
    kind: "cancel",
    state,
    issued_at: "2026-10-24T00:00:00+00:00",
    issuer: "user-1",
    task_ref: { kind: "task", issuer_domain: "platform", id: "task-1" },
    run_ref: null,
    expires_at: null,
    expected_revision: 2,
  };
}

function emptyPage(): TaskCommandsPage {
  return { items: [], total: 0 };
}

function issueReceipt(overrides: Partial<CommandIssueResponse> = {}): CommandIssueResponse {
  return {
    command_id: "server-cancel-1",
    kind: "cancel",
    state: "applied",
    issued_at: "2026-10-24T00:01:00+00:00",
    issuer: "user-1",
    task_ref: { kind: "task", issuer_domain: "platform", id: "task-1" },
    run_ref: null,
    expires_at: null,
    expected_revision: 2,
    ...overrides,
  };
}

beforeEach(() => {
  apiMock.get.mockReset();
  apiMock.post.mockReset();
});

describe("TaskDetailPage cancellation loop", () => {
  it("refreshes the Task and the receipt list after issuing a cancel", async () => {
    let detailCalls = 0;
    let commandListCalls = 0;
    apiMock.get.mockImplementation((path: string) => {
      if (path === "/api/tasks/task-1") {
        detailCalls += 1;
        // The re-query after the command reports the cancelled fact.
        return Promise.resolve(
          detail(detailCalls === 1 ? {} : { lifecycle_state: "cancelled", revision: 3 }),
        );
      }
      if (path.startsWith("/api/tasks/task-1/commands")) {
        commandListCalls += 1;
        // The list re-read after the command carries the recorded receipt.
        return Promise.resolve(
          commandListCalls === 1
            ? emptyPage()
            : { items: [commandRow("server-cancel-1", "applied")], total: 1 },
        );
      }
      return Promise.reject(new Error(`unexpected path: ${path}`));
    });
    apiMock.post.mockResolvedValueOnce(
      issueReceipt({ state: "applied", detail: "cancel applied before dispatch (receipt applied)" }),
    );
    render(<TaskDetailPage />);
    await screen.findByTestId("task-cancel-panel");

    // Pre-cancel: running badge, empty receipt list.
    expect(screen.getByTestId("task-summary")).toHaveTextContent("执行中");
    expect(await screen.findByTestId("task-commands-empty")).toBeInTheDocument();

    await userEvent.click(screen.getByTestId("task-cancel-request"));
    await userEvent.click(screen.getByTestId("task-cancel-confirm-accept"));

    // The receipt the page holds from the submission itself.
    expect(await screen.findByTestId("task-cancel-receipt")).toBeInTheDocument();
    expect(screen.getByTestId("task-cancel-receipt-state")).toHaveTextContent("已应用");
    // Lifecycle changed via the re-queried detail, not from the receipt.
    expect(await screen.findByTestId("task-summary")).toHaveTextContent("已取消");
    // The receipt list re-read shows the recorded server fact.
    expect(await screen.findByTestId("task-command-state")).toHaveTextContent("已应用");

    const detailPaths = apiMock.get.mock.calls
      .map((call) => call[0] as string)
      .filter((path) => path === "/api/tasks/task-1");
    expect(detailPaths).toHaveLength(2);
    expect(apiMock.post).toHaveBeenCalledTimes(1);
    const [url, body] = apiMock.post.mock.calls[0];
    expect(url).toBe("/api/tasks/task-1/commands");
    expect(body.kind).toBe("cancel");
    expect(body.expected_revision).toBe(2);
    expect(typeof body.command_id).toBe("string");
  });

  it("keeps the received receipt when the post-submit refresh fails", async () => {
    let detailCalls = 0;
    apiMock.get.mockImplementation((path: string) => {
      if (path === "/api/tasks/task-1") {
        detailCalls += 1;
        if (detailCalls === 1) return Promise.resolve(detail());
        return Promise.reject({
          detail: { error: { code: "DB_DOWN", message: "task detail query failed" } },
        });
      }
      if (path.startsWith("/api/tasks/task-1/commands")) return Promise.resolve(emptyPage());
      return Promise.reject(new Error(`unexpected path: ${path}`));
    });
    apiMock.post.mockResolvedValueOnce(
      issueReceipt({ state: "requested", detail: "running execution has no cooperative abort channel" }),
    );
    render(<TaskDetailPage />);
    await screen.findByTestId("task-cancel-panel");

    await userEvent.click(screen.getByTestId("task-cancel-request"));
    await userEvent.click(screen.getByTestId("task-cancel-confirm-accept"));

    // Refresh failure surfaces as a banner over the kept content — the full
    // error view would read as a failed submission and invite a new command.
    expect(await screen.findByTestId("task-refresh-error")).toHaveTextContent("DB_DOWN");
    expect(screen.getByTestId("task-cancel-receipt")).toBeInTheDocument();
    expect(screen.getByTestId("task-cancel-receipt-state")).toHaveTextContent("已请求");
    expect(screen.getByTestId("task-summary")).toHaveTextContent("执行中");
    expect(screen.queryByTestId("task-error")).not.toBeInTheDocument();
    expect(apiMock.post).toHaveBeenCalledTimes(1);
  });
});
