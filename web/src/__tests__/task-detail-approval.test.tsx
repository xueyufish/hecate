// Task detail page approval loop: the waiting_approval task shows the
// whitelisted wait summary; a frozen decision operation posts once and
// retries with the same operation id on unknown network results; decision
// facts and resume receipts render as separate facts; consumed waits and
// non-approval shapes render read-only.

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
import type { ApprovalView } from "@/lib/task-approval-types";

function detail(overrides: Record<string, unknown> = {}) {
  return {
    task_id: "task-1",
    goal: "创建工单",
    issuer_domain: "platform",
    workspace_id: "ws-1",
    lifecycle_state: "waiting_approval",
    revision: 4,
    created_at: "2026-10-29T00:00:00+00:00",
    runs: [],
    wait: {
      wake_kind: "resume",
      contract_ref: {
        approval_ref: "task:t1:ref1",
        tool_name: "create_ticket",
        risk_level: "high",
        target_resources: ["tickets/42"],
        invocation_id: "inv-1",
      },
      wait_token: "tok-1",
      wait_expires_at: null,
      consumed: false,
    },
    ...overrides,
  };
}

function approvalView(overrides: Partial<ApprovalView> = {}): ApprovalView {
  return {
    task_id: "task-1",
    workspace_id: "ws-1",
    wait: { consumed: false, expires_at: null, wait_token: "tok-1" },
    contract: {
      approval_ref: "task:t1:ref1",
      tool_name: "create_ticket",
      risk_level: "high",
      target_resources: ["tickets/42"],
      invocation_id: "inv-1",
    },
    decision: null,
    revision: 4,
    can_decide: true,
    read_only_reason: null,
    authority: { judgment: "pending", decided_by: null, expires_at: null },
    ...overrides,
  };
}

beforeEach(() => {
  apiMock.get.mockReset();
  apiMock.post.mockReset();
  apiMock.get.mockImplementation((path: string) => {
    if (path === "/api/tasks/task-1") return Promise.resolve(detail());
    if (path === "/api/tasks/task-1/approval") return Promise.resolve(approvalView());
    if (path.startsWith("/api/tasks/task-1/commands")) return Promise.resolve({ items: [], total: 0 });
    return Promise.reject(new Error(`unexpected GET ${path}`));
  });
});

function resumeReceipt() {
  return {
    command_id: "approval-resume-op-1",
    kind: "resume",
    state: "applied",
    issued_at: "2026-10-29T00:01:00+00:00",
    issuer: "approval-decision:admin-1",
    detail: "wake accepted; task requeued",
  };
}

describe("TaskDetailPage approval loop", () => {
  it("posts a frozen decision operation and renders decision and resume facts separately", async () => {
    const user = userEvent.setup();
    apiMock.post.mockResolvedValue({
      decision: {
        operation_id: "op-1",
        decision: "approve",
        reason: "justified",
        decided_by: "admin-1",
        created_at: "2026-10-29T00:01:00+00:00",
        resume_command_id: "approval-resume-op-1",
      },
      resume: resumeReceipt(),
    });
    render(<TaskDetailPage />);
    expect(await screen.findByTestId("task-approval-wait-summary")).toHaveTextContent("create_ticket");
    expect(screen.getByTestId("task-approval-target-resources")).toHaveTextContent("tickets/42");

    await user.type(screen.getByTestId("task-approval-reason"), "justified");
    await user.click(screen.getByTestId("task-approval-approve"));
    await user.click(screen.getByTestId("task-approval-confirm-accept"));

    expect(apiMock.post).toHaveBeenCalledTimes(1);
    const [path, body] = apiMock.post.mock.calls[0];
    expect(path).toBe("/api/tasks/task-1/approval");
    expect(body.decision).toBe("approve");
    expect(body.approval_ref).toBe("task:t1:ref1");
    expect(body.wait_token).toBe("tok-1");
    expect(body.expected_revision).toBe(4);
    expect(typeof body.operation_id).toBe("string");

    expect(await screen.findByTestId("task-approval-receipt-decision")).toHaveTextContent("approve");
    expect(screen.getByTestId("task-approval-resume-state")).toHaveTextContent("已应用");
    expect(screen.getByTestId("task-approval-resume-detail")).toHaveTextContent("wake accepted");
  });

  it("keeps the operation id on unknown network results and retries the same body", async () => {
    const user = userEvent.setup();
    apiMock.post
      .mockRejectedValueOnce(new Error("network down"))
      .mockResolvedValueOnce({
        decision: {
          operation_id: "op-1",
          decision: "approve",
          reason: "",
          decided_by: "admin-1",
          created_at: null,
          resume_command_id: "approval-resume-op-1",
        },
        resume: resumeReceipt(),
      });
    render(<TaskDetailPage />);
    await screen.findByTestId("task-approval-wait-summary");
    await user.click(screen.getByTestId("task-approval-approve"));
    await user.click(screen.getByTestId("task-approval-confirm-accept"));

    expect(await screen.findByTestId("task-approval-failure")).toHaveTextContent("网络");
    const failedId = screen.getByTestId("task-approval-operation-id").textContent;
    await user.click(screen.getByTestId("task-approval-retry"));
    await screen.findByTestId("task-approval-receipt");
    expect(apiMock.post).toHaveBeenCalledTimes(2);
    expect(apiMock.post.mock.calls[1][1].operation_id).toBe(failedId);
    expect(apiMock.post.mock.calls[1][1].decision).toBe("approve");
  });

  it("renders a consumed approval wait read-only", async () => {
    apiMock.get.mockImplementation((path: string) => {
      if (path === "/api/tasks/task-1")
        return Promise.resolve(detail({ wait: { ...detail().wait, consumed: true } }));
      if (path === "/api/tasks/task-1/approval")
        return Promise.resolve(approvalView({ wait: { ...approvalView().wait, consumed: true } }));
      return Promise.resolve({ items: [], total: 0 });
    });
    render(<TaskDetailPage />);
    expect(await screen.findByTestId("task-approval-wait-summary")).toHaveTextContent("已消费");
    expect(screen.queryByTestId("task-approval-form")).not.toBeInTheDocument();
  });

  it("renders non-approval tasks without an approval surface", async () => {
    apiMock.get.mockImplementation((path: string) => {
      if (path === "/api/tasks/task-1")
        return Promise.resolve(detail({ lifecycle_state: "running", wait: undefined }));
      return Promise.resolve({ items: [], total: 0 });
    });
    render(<TaskDetailPage />);
    expect(await screen.findByTestId("task-approval-not-waiting")).toHaveTextContent("没有待决定的审批");
    expect(screen.queryByTestId("task-approval-form")).not.toBeInTheDocument();
    expect(screen.queryByTestId("task-approval-wait-info")).not.toBeInTheDocument();
  });
});
