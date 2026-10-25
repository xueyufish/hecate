// TaskCancelPanel behavior: confirmation before any network traffic, one
// stable command_id per operation (reused across retries and duplicate
// clicks), verbatim receipt states with their real meanings, structured vs
// unknown-result failures, revision-rejection without auto re-send, disabled
// submission for terminal/unversioned tasks, and dropped late responses
// after unmount.

import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TaskCancelPanel } from "@/components/tasks/task-cancel-panel";
import type { CommandIssueResponse } from "@/lib/task-command-types";

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("@/lib/api-client", () => ({ api: apiMock }));

function receipt(overrides: Partial<CommandIssueResponse> = {}): CommandIssueResponse {
  return {
    command_id: "server-id-1",
    kind: "cancel",
    state: "applied",
    issued_at: "2026-10-24T00:00:00+00:00",
    issuer: "user-1",
    task_ref: { kind: "task", issuer_domain: "platform", id: "task-1" },
    run_ref: null,
    expires_at: null,
    expected_revision: 3,
    ...overrides,
  };
}

async function confirmCancel() {
  await userEvent.click(screen.getByTestId("task-cancel-request"));
  await userEvent.click(screen.getByTestId("task-cancel-confirm-accept"));
}

const ISSUE_URL = "/api/tasks/task-1/commands";

beforeEach(() => {
  apiMock.post.mockReset();
});

describe("TaskCancelPanel submission semantics", () => {
  it("sends kind=cancel with a stable command_id and the page-read revision", async () => {
    apiMock.post.mockResolvedValueOnce(
      receipt({ state: "applied", detail: "cancel applied before dispatch (receipt applied)" }),
    );
    const onIssued = vi.fn();
    render(
      <TaskCancelPanel taskId="task-1" lifecycleState="queued" revision={3} onIssued={onIssued} />,
    );

    await confirmCancel();

    expect(apiMock.post).toHaveBeenCalledTimes(1);
    const [url, body] = apiMock.post.mock.calls[0];
    expect(url).toBe(ISSUE_URL);
    expect(body.kind).toBe("cancel");
    expect(body.expected_revision).toBe(3);
    expect(typeof body.command_id).toBe("string");
    expect(body.command_id.length).toBeGreaterThan(0);

    expect(await screen.findByTestId("task-cancel-receipt")).toBeInTheDocument();
    expect(screen.getByTestId("task-cancel-receipt-state")).toHaveTextContent("已应用");
    expect(screen.getByTestId("task-cancel-receipt-detail")).toHaveTextContent(
      "cancel applied before dispatch",
    );
    expect(onIssued).toHaveBeenCalledTimes(1);
  });

  it("requires explicit confirmation before any request", async () => {
    render(<TaskCancelPanel taskId="task-1" lifecycleState="queued" revision={0} />);
    await userEvent.click(screen.getByTestId("task-cancel-request"));
    expect(apiMock.post).not.toHaveBeenCalled();
    // Abandoning the confirmation never submits either.
    await userEvent.click(screen.getByTestId("task-cancel-confirm-cancel"));
    expect(screen.getByTestId("task-cancel-request")).toBeInTheDocument();
    expect(apiMock.post).not.toHaveBeenCalled();
  });

  it("blocks duplicate submission while a request is in flight", async () => {
    let resolvePost!: (value: CommandIssueResponse) => void;
    apiMock.post.mockReturnValueOnce(
      new Promise<CommandIssueResponse>((resolve) => {
        resolvePost = resolve;
      }),
    );
    render(<TaskCancelPanel taskId="task-1" lifecycleState="running" revision={2} />);

    await confirmCancel();
    expect(screen.getByTestId("task-cancel-submitting")).toBeInTheDocument();
    // No button is available mid-flight; a re-render cannot fire a second call.
    expect(screen.queryByTestId("task-cancel-request")).not.toBeInTheDocument();
    expect(screen.queryByTestId("task-cancel-retry")).not.toBeInTheDocument();

    await act(async () => {
      resolvePost(receipt({ state: "requested", expected_revision: 2 }));
    });
    expect(await screen.findByTestId("task-cancel-receipt")).toBeInTheDocument();
    expect(apiMock.post).toHaveBeenCalledTimes(1);
  });

  it("reuses the same command_id and body when retrying an unknown network result", async () => {
    apiMock.post
      .mockRejectedValueOnce(new TypeError("fetch failed"))
      .mockResolvedValueOnce(receipt({ state: "requested", expected_revision: 5 }));
    render(<TaskCancelPanel taskId="task-1" lifecycleState="running" revision={5} />);

    await confirmCancel();
    expect(await screen.findByTestId("task-cancel-failure")).toBeInTheDocument();
    expect(screen.getByTestId("task-cancel-failure")).toHaveTextContent("fetch failed");
    expect(screen.getByTestId("task-cancel-failure")).toHaveTextContent("复用同一命令 ID");

    await userEvent.click(screen.getByTestId("task-cancel-retry"));
    expect(await screen.findByTestId("task-cancel-receipt")).toBeInTheDocument();

    expect(apiMock.post).toHaveBeenCalledTimes(2);
    const [firstUrl, firstBody] = apiMock.post.mock.calls[0];
    const [secondUrl, secondBody] = apiMock.post.mock.calls[1];
    expect(firstUrl).toBe(secondUrl);
    expect(secondBody).toEqual(firstBody);
  });

  it.each(["FORBIDDEN", "NOT_FOUND"])("surfaces %s and keeps the retry idempotent", async (code) => {
    apiMock.post
      .mockRejectedValueOnce({
        detail: { error: { code, message: "request denied" } },
      })
      .mockRejectedValueOnce({
        detail: { error: { code, message: "request denied" } },
      });
    render(<TaskCancelPanel taskId="task-1" lifecycleState="queued" revision={1} />);

    await confirmCancel();
    const failure = await screen.findByTestId("task-cancel-failure");
    expect(failure).toHaveTextContent(code);
    expect(failure).toHaveTextContent("request denied");

    await userEvent.click(screen.getByTestId("task-cancel-retry"));
    expect(await screen.findByTestId("task-cancel-failure"));
    expect(apiMock.post).toHaveBeenCalledTimes(2);
    expect(apiMock.post.mock.calls[1][1]).toEqual(apiMock.post.mock.calls[0][1]);
  });

  it("keeps the original request body when the page revision changes before retry", async () => {
    apiMock.post.mockRejectedValueOnce(new TypeError("fetch failed"))
      .mockResolvedValueOnce(receipt({ state: "requested" }));
    const { rerender } = render(
      <TaskCancelPanel taskId="task-1" lifecycleState="running" revision={2} />,
    );
    await confirmCancel();
    await screen.findByTestId("task-cancel-retry");
    rerender(<TaskCancelPanel taskId="task-1" lifecycleState="running" revision={3} />);
    await userEvent.click(screen.getByTestId("task-cancel-retry"));
    await screen.findByTestId("task-cancel-receipt");
    expect(apiMock.post.mock.calls[1]).toEqual(apiMock.post.mock.calls[0]);
  });

  it("keeps the confirmed revision visible when the page refreshes during confirmation", async () => {
    apiMock.post.mockResolvedValueOnce(receipt({ state: "requested" }));
    const { rerender } = render(
      <TaskCancelPanel taskId="task-1" lifecycleState="running" revision={2} />,
    );
    await userEvent.click(screen.getByTestId("task-cancel-request"));
    rerender(<TaskCancelPanel taskId="task-1" lifecycleState="running" revision={3} />);
    expect(screen.getByTestId("task-cancel-confirm")).toHaveTextContent("revision 2");
    await userEvent.click(screen.getByTestId("task-cancel-confirm-accept"));
    expect(apiMock.post.mock.calls[0][1].expected_revision).toBe(2);
  });

  it("withdraws confirmation when a refreshed task has become terminal", async () => {
    const { rerender } = render(
      <TaskCancelPanel taskId="task-1" lifecycleState="running" revision={2} />,
    );
    await userEvent.click(screen.getByTestId("task-cancel-request"));
    rerender(<TaskCancelPanel taskId="task-1" lifecycleState="succeeded" revision={3} />);
    expect(screen.queryByTestId("task-cancel-confirm-accept")).not.toBeInTheDocument();
    expect(apiMock.post).not.toHaveBeenCalled();
  });

  it("shows a revision-rejected receipt with a refresh hint and never auto re-sends", async () => {
    apiMock.post.mockResolvedValueOnce(
      receipt({ state: "rejected", detail: "expected task revision no longer matches" }),
    );
    render(<TaskCancelPanel taskId="task-1" lifecycleState="queued" revision={4} />);

    await confirmCancel();
    expect(await screen.findByTestId("task-cancel-receipt")).toBeInTheDocument();
    expect(screen.getByTestId("task-cancel-receipt-state")).toHaveTextContent("已拒绝");
    expect(screen.getByTestId("task-cancel-revision-hint")).toHaveTextContent("请刷新");
    expect(apiMock.post).toHaveBeenCalledTimes(1);
  });

  it("renders a running task's requested receipt verbatim with its meaning", async () => {
    apiMock.post.mockResolvedValueOnce(
      receipt({
        state: "requested",
        detail:
          "running execution has no cooperative abort channel; the command stays requested and the task runs to its outcome",
        expected_revision: 7,
      }),
    );
    render(<TaskCancelPanel taskId="task-1" lifecycleState="running" revision={7} />);

    await confirmCancel();
    expect(await screen.findByTestId("task-cancel-receipt")).toBeInTheDocument();
    expect(screen.getByTestId("task-cancel-receipt-state")).toHaveTextContent("已请求");
    expect(screen.getByTestId("task-cancel-receipt-meaning")).toHaveTextContent("尚未确认");
    expect(screen.getByTestId("task-cancel-receipt-detail")).toHaveTextContent(
      "no cooperative abort channel",
    );
    // The panel never claims the Task became cancelled from the receipt.
    expect(screen.queryByText("已取消")).not.toBeInTheDocument();
  });

  it("drops a late response after unmount and never fires onIssued", async () => {
    let resolvePost!: (value: CommandIssueResponse) => void;
    apiMock.post.mockReturnValueOnce(
      new Promise<CommandIssueResponse>((resolve) => {
        resolvePost = resolve;
      }),
    );
    const onIssued = vi.fn();
    const { unmount } = render(
      <TaskCancelPanel taskId="task-1" lifecycleState="queued" revision={2} onIssued={onIssued} />,
    );

    await confirmCancel();
    unmount();

    await act(async () => {
      resolvePost(receipt({ state: "applied" }));
    });
    expect(onIssued).not.toHaveBeenCalled();
    expect(apiMock.post).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["requested", "已请求"], ["acknowledged", "已确认接收"],
    ["applied", "已应用"], ["rejected", "已拒绝"], ["expired", "已过期"],
  ])("renders the recorded %s receipt without changing lifecycle", async (state, label) => {
    apiMock.post.mockResolvedValueOnce(receipt({ state }));
    render(<TaskCancelPanel taskId="task-1" lifecycleState="running" revision={2} />);
    await confirmCancel();
    expect(await screen.findByTestId("task-cancel-receipt-state")).toHaveTextContent(label);
    expect(screen.queryByTestId("task-cancel-terminal")).not.toBeInTheDocument();
  });

  it("isolates a new Task visit from the previous Task's pending cancellation", async () => {
    let resolvePost!: (value: CommandIssueResponse) => void;
    apiMock.post.mockReturnValueOnce(new Promise<CommandIssueResponse>((resolve) => { resolvePost = resolve; }));
    const onIssued = vi.fn();
    const { rerender } = render(
      <TaskCancelPanel taskId="task-1" lifecycleState="running" revision={2} onIssued={onIssued} />,
    );
    await confirmCancel();
    rerender(<TaskCancelPanel taskId="task-2" lifecycleState="queued" revision={0} onIssued={onIssued} />);
    await act(async () => { resolvePost(receipt()); });
    expect(screen.queryByTestId("task-cancel-receipt")).not.toBeInTheDocument();
    expect(screen.getByTestId("task-cancel-request")).toBeInTheDocument();
    expect(onIssued).not.toHaveBeenCalled();
  });
});

describe("TaskCancelPanel disabled submission scopes", () => {
  it.each(["succeeded", "failed", "cancelled"])(
    "forbids cancelling a terminal task (%s) and sends nothing",
    async (terminal) => {
      render(<TaskCancelPanel taskId="task-1" lifecycleState={terminal} revision={9} />);
      expect(screen.getByTestId("task-cancel-terminal")).toBeInTheDocument();
      expect(screen.queryByTestId("task-cancel-request")).not.toBeInTheDocument();
      expect(apiMock.post).not.toHaveBeenCalled();
    },
  );

  it("forbids cancelling without a recorded revision", () => {
    render(<TaskCancelPanel taskId="task-1" lifecycleState="unrecorded" revision={null} />);
    expect(screen.getByTestId("task-cancel-no-revision")).toBeInTheDocument();
    expect(screen.queryByTestId("task-cancel-request")).not.toBeInTheDocument();
  });

  it("stops offering retries once the task turns terminal after a refresh", async () => {
    apiMock.post.mockRejectedValueOnce(new TypeError("fetch failed"));
    const { rerender } = render(
      <TaskCancelPanel taskId="task-1" lifecycleState="running" revision={2} />,
    );
    await confirmCancel();
    expect(await screen.findByTestId("task-cancel-retry")).toBeInTheDocument();

    // The page re-queried and the task reached a terminal state.
    rerender(
      <TaskCancelPanel taskId="task-1" lifecycleState="succeeded" revision={3} />,
    );
    expect(screen.queryByTestId("task-cancel-retry")).not.toBeInTheDocument();
    expect(screen.getByTestId("task-cancel-terminal")).toBeInTheDocument();
  });
});
