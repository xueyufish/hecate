import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("@/lib/api-client", () => ({ api: apiMock }));

import { TaskInputPanel } from "@/components/tasks/task-input-panel";
import type { TaskDetail } from "@/lib/task-types";

function detail(token = "wait-1", revision = 3): TaskDetail {
  return {
    task_id: "task-1", goal: "test", issuer_domain: "hecate", workspace_id: "ws-1",
    lifecycle_state: "waiting_input", revision, created_at: null, runs: [],
    wait: {
      wake_kind: "provide_input", wait_token: token, consumed: false, wait_expires_at: null,
      contract_ref: { kind: "input", tool: "query", contract: { fields: ["region"], prompt: "Select a region" } },
    },
  };
}

const receipt = {
  command_id: "server-id", kind: "provide_input", state: "applied", issued_at: "now", issuer: "user",
  task_ref: { kind: "task", issuer_domain: "hecate", id: "task-1" }, detail: "accepted",
};

async function confirmInput() {
  fireEvent.change(screen.getByTestId("task-input-textarea"), { target: { value: '{"region":"EU"}' } });
  await userEvent.click(screen.getByTestId("task-input-parse"));
}

beforeEach(() => { apiMock.post.mockReset(); });

describe("TaskInputPanel operation boundaries", () => {
  it("retries the complete frozen body after the task enters a different wait", async () => {
    apiMock.post.mockRejectedValueOnce(new Error("network lost")).mockResolvedValueOnce(receipt);
    const onIssued = vi.fn();
    const view = render(<TaskInputPanel taskId="task-1" detail={detail()} onIssued={onIssued} />);
    await confirmInput();
    await userEvent.click(screen.getByTestId("task-input-confirm-accept"));
    await screen.findByTestId("task-input-failure");
    const first = apiMock.post.mock.calls[0][1];
    view.rerender(<TaskInputPanel taskId="task-1" detail={detail("wait-2", 9)} onIssued={onIssued} />);
    await userEvent.click(screen.getByTestId("task-input-retry"));
    await screen.findByTestId("task-input-receipt");
    expect(apiMock.post.mock.calls[1][1]).toEqual(first);
    expect(onIssued).toHaveBeenCalledTimes(1);
  });

  it("can resolve an unknown receipt after the task becomes terminal", async () => {
    apiMock.post.mockRejectedValueOnce(new Error("network lost")).mockResolvedValueOnce(receipt);
    const view = render(<TaskInputPanel taskId="task-1" detail={detail()} />);
    await confirmInput();
    await userEvent.click(screen.getByTestId("task-input-confirm-accept"));
    await screen.findByTestId("task-input-failure");
    view.rerender(<TaskInputPanel taskId="task-1" detail={{ ...detail(), lifecycle_state: "succeeded" }} />);
    await userEvent.click(screen.getByTestId("task-input-retry"));
    await screen.findByTestId("task-input-receipt");
    expect(apiMock.post.mock.calls[1][1]).toEqual(apiMock.post.mock.calls[0][1]);
  });

  it("starts a new operation for a later wait on the same task", async () => {
    apiMock.post.mockResolvedValue(receipt);
    const view = render(<TaskInputPanel taskId="task-1" detail={detail()} />);
    await confirmInput();
    await userEvent.click(screen.getByTestId("task-input-confirm-accept"));
    await screen.findByTestId("task-input-receipt");
    const first = apiMock.post.mock.calls[0][1];
    view.rerender(<TaskInputPanel taskId="task-1" detail={detail("wait-2", 9)} />);
    await userEvent.click(screen.getByTestId("task-input-new-operation"));
    await confirmInput();
    await userEvent.click(screen.getByTestId("task-input-confirm-accept"));
    await screen.findByTestId("task-input-receipt");
    const next = apiMock.post.mock.calls[1][1];
    expect(next.command_id).not.toBe(first.command_id);
    expect(next.expected_revision).toBe(9);
    expect(next.detail_ns.wait_token).toBe("wait-2");
  });

  it("allows corrected input after a rejected contract", async () => {
    apiMock.post.mockResolvedValue({ ...receipt, state: "rejected", detail: "wait contract unsatisfied" });
    render(<TaskInputPanel taskId="task-1" detail={detail()} />);
    await confirmInput();
    await userEvent.click(screen.getByTestId("task-input-confirm-accept"));
    await screen.findByTestId("task-input-receipt");
    await userEvent.click(screen.getByTestId("task-input-new-operation"));
    expect(screen.getByTestId("task-input-textarea")).toHaveValue("");
  });

  it("requires a new confirmation if the wait changes before submission", async () => {
    const view = render(<TaskInputPanel taskId="task-1" detail={detail()} />);
    await confirmInput();
    view.rerender(<TaskInputPanel taskId="task-1" detail={detail("wait-2", 9)} />);
    await userEvent.click(screen.getByTestId("task-input-confirm-accept"));
    expect(apiMock.post).not.toHaveBeenCalled();
    expect(screen.getByTestId("task-input-parse-error")).toHaveTextContent("等待信息已变化");
  });

  it.each([
    { wait: { wake_kind: "provide_input", wait_token: "token", consumed: false } },
    { wait: { ...detail().wait, contract_ref: null } },
    { wait: { ...detail().wait, contract_ref: { kind: "future", tool: "query", contract: { fields: ["region"] } } } },
    { wait: { ...detail().wait, wait_expires_at: "2000-01-01T00:00:00Z" } },
    { wait: { ...detail().wait, wait_expires_at: "bad date" } },
    { wait: { ...detail().wait, wait_expires_at: "" } },
    { wait: { ...detail().wait, consumed: undefined } },
    { wait: { ...detail().wait, contract_ref: { kind: "input", tool: "query", contract: { fields: ["approval_ref"] } } } },
    { revision: null },
    { wait: undefined },
  ])("keeps unsupported or expired waits read-only: %j", (override) => {
    render(<TaskInputPanel taskId="task-1" detail={{ ...detail(), ...override }} />);
    expect(screen.queryByTestId("task-input-form")).not.toBeInTheDocument();
    expect(screen.getByTestId("task-input-read-only")).not.toHaveTextContent(/^$/);
  });

  it("does not submit a second command on a double click", async () => {
    apiMock.post.mockReturnValue(new Promise(() => {}));
    render(<TaskInputPanel taskId="task-1" detail={detail()} />);
    await confirmInput();
    await userEvent.dblClick(screen.getByTestId("task-input-confirm-accept"));
    expect(apiMock.post).toHaveBeenCalledTimes(1);
  });
});
