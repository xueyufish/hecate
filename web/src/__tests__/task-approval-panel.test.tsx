import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("@/lib/api-client", () => ({ api: apiMock }));

import { TaskApprovalPanel } from "@/components/tasks/task-approval-panel";
import type { ApprovalView } from "@/lib/task-approval-types";
import type { TaskDetail } from "@/lib/task-types";

function detail(token = "wait-1", revision = 4): TaskDetail {
  return { task_id: "task-1", workspace_id: "ws-1", issuer_domain: "hecate", goal: "test", created_at: null,
    lifecycle_state: "waiting_approval", revision, runs: [], wait: {
      wake_kind: "resume", consumed: false, wait_token: token, wait_expires_at: null,
      contract_ref: { approval_ref: `ref-${token}`, tool_name: "write", target_resources: [], invocation_id: token },
    } };
}

function view(token = "wait-1", revision = 4): ApprovalView {
  return { task_id: "task-1", workspace_id: "ws-1", revision, can_decide: true, read_only_reason: null,
    authority: { judgment: "pending", decided_by: null, expires_at: null }, decision: null,
    wait: { wait_token: token, expires_at: null, consumed: false },
    contract: { approval_ref: `ref-${token}`, tool_name: "write", risk_level: "high", target_resources: [], invocation_id: token } };
}

const response = {
  decision: { operation_id: "server-op", decision: "approve", reason: "ok", decided_by: "admin" },
  resume: { command_id: "resume-op", kind: "resume", state: "applied", issued_at: "now", issuer: "admin" },
};

async function confirm() {
  await userEvent.click(await screen.findByTestId("task-approval-approve"));
  await userEvent.click(screen.getByTestId("task-approval-confirm-accept"));
}

beforeEach(() => {
  apiMock.get.mockReset().mockResolvedValue(view());
  apiMock.post.mockReset().mockResolvedValue(response);
});

describe("TaskApprovalPanel boundaries", () => {
  it("replays the frozen body after the task becomes terminal", async () => {
    apiMock.post.mockRejectedValueOnce(new Error("network lost")).mockResolvedValueOnce(response);
    const panel = render(<TaskApprovalPanel taskId="task-1" detail={detail()} />);
    await confirm();
    await screen.findByTestId("task-approval-failure");
    const body = apiMock.post.mock.calls[0][1];
    panel.rerender(<TaskApprovalPanel taskId="task-1" detail={{ ...detail(), lifecycle_state: "succeeded" }} />);
    await userEvent.click(screen.getByTestId("task-approval-retry"));
    await screen.findByTestId("task-approval-receipt");
    expect(apiMock.post.mock.calls[1][1]).toEqual(body);
  });

  it("starts another operation for a fresh approval on the same task", async () => {
    const panel = render(<TaskApprovalPanel taskId="task-1" detail={detail()} />);
    await confirm();
    await screen.findByTestId("task-approval-receipt");
    const first = apiMock.post.mock.calls[0][1];
    apiMock.get.mockResolvedValue(view("wait-2", 9));
    panel.rerender(<TaskApprovalPanel taskId="task-1" detail={detail("wait-2", 9)} />);
    await userEvent.click(await screen.findByTestId("task-approval-new-operation"));
    await confirm();
    expect(apiMock.post.mock.calls[1][1]).toMatchObject({ approval_ref: "ref-wait-2", wait_token: "wait-2", expected_revision: 9 });
    expect(apiMock.post.mock.calls[1][1].operation_id).not.toBe(first.operation_id);
  });

  it.each(["ADMIN_REQUIRED", "SELF_APPROVAL", "ALREADY_DECIDED", "BINDING_INVALID"])("keeps %s read-only", async (reason) => {
    apiMock.get.mockResolvedValue({ ...view(), can_decide: false, read_only_reason: reason });
    render(<TaskApprovalPanel taskId="task-1" detail={detail()} />);
    await waitFor(() => expect(screen.getByTestId("task-approval-read-only")).not.toHaveTextContent("未知"));
    expect(screen.queryByTestId("task-approval-form")).not.toBeInTheDocument();
  });

  it.each(["2000-01-01T00:00:00Z", "invalid"])("keeps expiry %s read-only", async (expires_at) => {
    apiMock.get.mockResolvedValue({ ...view(), wait: { ...view().wait, expires_at } });
    render(<TaskApprovalPanel taskId="task-1" detail={detail()} />);
    await waitFor(() => expect(screen.getByTestId("task-approval-read-only")).toHaveTextContent("期限"));
    expect(screen.queryByTestId("task-approval-form")).not.toBeInTheDocument();
  });

  it("ignores a stale approval query after a new wait arrives", async () => {
    let resolveOld: ((value: ApprovalView) => void) | undefined;
    apiMock.get.mockImplementationOnce(() => new Promise<ApprovalView>((resolve) => { resolveOld = resolve; }));
    const panel = render(<TaskApprovalPanel taskId="task-1" detail={detail()} />);
    apiMock.get.mockResolvedValue(view("wait-2", 9));
    panel.rerender(<TaskApprovalPanel taskId="task-1" detail={detail("wait-2", 9)} />);
    await screen.findByTestId("task-approval-form");
    resolveOld?.(view());
    await confirm();
    expect(apiMock.post.mock.calls[0][1]).toMatchObject({ approval_ref: "ref-wait-2", wait_token: "wait-2" });
  });

  it("invalidates confirmation when the observed wait changes", async () => {
    const panel = render(<TaskApprovalPanel taskId="task-1" detail={detail()} />);
    await userEvent.click(await screen.findByTestId("task-approval-approve"));
    apiMock.get.mockResolvedValue(view("wait-2", 9));
    panel.rerender(<TaskApprovalPanel taskId="task-1" detail={detail("wait-2", 9)} />);
    await userEvent.click(await screen.findByTestId("task-approval-confirm-accept"));
    expect(apiMock.post).not.toHaveBeenCalled();
  });

  it("does not run callbacks after leaving the task", async () => {
    let resolvePost: ((value: typeof response) => void) | undefined;
    apiMock.post.mockImplementationOnce(() => new Promise<typeof response>((resolve) => { resolvePost = resolve; }));
    const onIssued = vi.fn();
    const panel = render(<TaskApprovalPanel taskId="task-1" detail={detail()} onIssued={onIssued} />);
    await confirm();
    panel.unmount();
    resolvePost?.(response);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(onIssued).not.toHaveBeenCalled();
  });

  it("renders a committed decision separately from pending recovery", async () => {
    apiMock.post.mockResolvedValue({ ...response, resume: { state: "pending_recovery" } });
    render(<TaskApprovalPanel taskId="task-1" detail={detail()} />);
    await confirm();
    expect(await screen.findByTestId("task-approval-receipt-decision")).toHaveTextContent("approve");
    expect(screen.getByTestId("task-approval-resume-state")).toHaveTextContent("等待恢复");
  });

  it("deduplicates double confirmation", async () => {
    apiMock.post.mockReturnValue(new Promise(() => {}));
    render(<TaskApprovalPanel taskId="task-1" detail={detail()} />);
    await userEvent.click(await screen.findByTestId("task-approval-approve"));
    await userEvent.dblClick(screen.getByTestId("task-approval-confirm-accept"));
    expect(apiMock.post).toHaveBeenCalledTimes(1);
  });
});
