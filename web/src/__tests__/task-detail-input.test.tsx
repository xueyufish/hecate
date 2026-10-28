// Task detail page input loop: the waiting_input task exposes the input
// panel; a confirmed submission posts kind=provide_input with a stable
// command_id, the page-read revision, the payload and the wait token in
// detail_ns; the issued command refreshes both the Task detail and the
// receipt list; and read-only waiting shapes never render a form.

import { fireEvent, render, screen } from "@testing-library/react";
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
import type { TaskCommandsPage } from "@/lib/task-command-types";

function detail(overrides: Record<string, unknown> = {}) {
  return {
    task_id: "task-1",
    goal: "核对区域库存",
    issuer_domain: "platform",
    workspace_id: "ws-1",
    lifecycle_state: "waiting_input",
    revision: 5,
    created_at: "2026-10-28T00:00:00+00:00",
    runs: [],
    wait: {
      wake_kind: "provide_input",
      contract_ref: { tool: "query_inventory", arguments: { sku: "S-9" }, kind: "input", contract: { fields: ["region"] } },
      wait_token: "token-1",
      wait_expires_at: null,
      consumed: false,
    },
    ...overrides,
  };
}

function emptyPage(): TaskCommandsPage {
  return { items: [], total: 0 };
}

beforeEach(() => {
  apiMock.get.mockReset();
  apiMock.post.mockReset();
  apiMock.get.mockImplementation((path: string) => {
    if (path === "/api/tasks/task-1") return Promise.resolve(detail());
    if (path.startsWith("/api/tasks/task-1/commands")) return Promise.resolve(emptyPage());
    return Promise.reject(new Error(`unexpected GET ${path}`));
  });
});

describe("TaskDetailPage input loop", () => {
  it("submits provide_input with the frozen id, revision, payload and wait token", async () => {
    const user = userEvent.setup();
    apiMock.post.mockResolvedValue({
      command_id: "server-input-1",
      kind: "provide_input",
      state: "applied",
      issued_at: "2026-10-28T00:01:00+00:00",
      issuer: "user-1",
      task_ref: { kind: "task", issuer_domain: "platform", id: "task-1" },
      run_ref: null,
      expires_at: null,
      expected_revision: 5,
      detail: "wake accepted; task requeued",
    });
    render(<TaskDetailPage />);
    expect(await screen.findByTestId("task-input-wait-summary")).toHaveTextContent("等待人工输入");
    expect(screen.getByTestId("task-input-wait-fields")).toHaveTextContent("region");

    fireEvent.change(screen.getByTestId("task-input-textarea"), { target: { value: '{"region": "EU"}' } });
    await user.click(screen.getByTestId("task-input-parse"));
    await user.click(screen.getByTestId("task-input-confirm-accept"));

    expect(apiMock.post).toHaveBeenCalledTimes(1);
    const [path, body] = apiMock.post.mock.calls[0];
    expect(path).toBe("/api/tasks/task-1/commands");
    expect(body.kind).toBe("provide_input");
    expect(body.expected_revision).toBe(5);
    expect(body.payload).toEqual({ region: "EU" });
    expect(body.payload_schema_ref).toBe("urn:hecate:task-user-input/0");
    expect(body.detail_ns).toEqual({ wait_token: "token-1" });
    expect(await screen.findByTestId("task-input-receipt-state")).toHaveTextContent("已应用");
    expect(screen.getByTestId("task-input-receipt-detail")).toHaveTextContent("wake accepted");
  });

  it("rejects non-object or invalid JSON before any network call", async () => {
    const user = userEvent.setup();
    render(<TaskDetailPage />);
    await screen.findByTestId("task-input-wait-summary");
    fireEvent.change(screen.getByTestId("task-input-textarea"), { target: { value: "[1,2]" } });
    await user.click(screen.getByTestId("task-input-parse"));
    expect(screen.getByTestId("task-input-parse-error")).toHaveTextContent("JSON 对象");
    expect(apiMock.post).not.toHaveBeenCalled();
  });

  it("renders a consumed wait read-only", async () => {
    apiMock.get.mockImplementation((path: string) =>
      path === "/api/tasks/task-1"
        ? Promise.resolve(detail({ wait: { ...detail().wait, consumed: true } }))
        : Promise.resolve(emptyPage()),
    );
    render(<TaskDetailPage />);
    expect(await screen.findByTestId("task-input-wait-summary")).toHaveTextContent("已消费");
    expect(screen.queryByTestId("task-input-form")).not.toBeInTheDocument();
  });

  it("renders an approval wait read-only", async () => {
    apiMock.get.mockImplementation((path: string) =>
      path === "/api/tasks/task-1"
        ? Promise.resolve(
            detail({
              lifecycle_state: "waiting_approval",
              wait: { wake_kind: "resume", contract_ref: { approval_ref: "a1" }, wait_token: "t2", consumed: false },
            }),
          )
        : Promise.resolve(emptyPage()),
    );
    render(<TaskDetailPage />);
    expect(await screen.findByTestId("task-input-not-waiting")).toHaveTextContent("等待审批");
    expect(screen.queryByTestId("task-input-form")).not.toBeInTheDocument();
    expect(screen.queryByTestId("task-input-wait-info")).not.toBeInTheDocument();
  });

  it("renders a workflow child wait read-only", async () => {
    apiMock.get.mockImplementation((path: string) =>
      path === "/api/tasks/task-1"
        ? Promise.resolve(
            detail({
              wait: {
                wake_kind: "provide_input",
                contract_ref: { await_task_ref: { issuer_domain: "hecate", id: "child", kind: "task" } },
                wait_token: "t3",
                consumed: false,
              },
            }),
          )
        : Promise.resolve(emptyPage()),
    );
    render(<TaskDetailPage />);
    expect(await screen.findByTestId("task-input-wait-summary")).toHaveTextContent("工作流子任务结果等待");
    expect(screen.queryByTestId("task-input-form")).not.toBeInTheDocument();
  });

  it("keeps the receipt and offers a same-id retry on unknown network results", async () => {
    const user = userEvent.setup();
    apiMock.post
      .mockRejectedValueOnce(new Error("network down"))
      .mockResolvedValueOnce({
        command_id: "server-input-9",
        kind: "provide_input",
        state: "applied",
        issued_at: "2026-10-28T00:02:00+00:00",
        issuer: "user-1",
        task_ref: { kind: "task", issuer_domain: "platform", id: "task-1" },
        run_ref: null,
        expires_at: null,
        expected_revision: 5,
        detail: "wake accepted; task requeued",
      });
    render(<TaskDetailPage />);
    await screen.findByTestId("task-input-wait-summary");
    fireEvent.change(screen.getByTestId("task-input-textarea"), { target: { value: '{"region": "EU"}' } });
    await user.click(screen.getByTestId("task-input-parse"));
    await user.click(screen.getByTestId("task-input-confirm-accept"));

    expect(await screen.findByTestId("task-input-failure")).toHaveTextContent("网络");
    const failedCommandId = screen.getByTestId("task-input-command-id").textContent;
    await user.click(screen.getByTestId("task-input-retry"));
    await screen.findByTestId("task-input-receipt");
    expect(apiMock.post).toHaveBeenCalledTimes(2);
    expect(apiMock.post.mock.calls[1][1].command_id).toBe(failedCommandId);
    expect(apiMock.post.mock.calls[1][1].payload).toEqual({ region: "EU" });
  });
});
