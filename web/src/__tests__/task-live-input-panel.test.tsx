// TaskLiveInputPanel behavior: server-derived capability only (no composer
// without detail.live_input), unsupported reasons rendered verbatim, the
// versioned envelope on the wire, stable command identity across retries,
// receipt facts that never read as effective/consumed, stale-revision
// refusal after a refresh, and local rejection of control-key content.

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("@/lib/api-client", () => ({ api: apiMock }));

import { TaskLiveInputPanel } from "@/components/tasks/task-live-input-panel";
import type { TaskDetail } from "@/lib/task-types";

function detail(overrides: Partial<TaskDetail> = {}): TaskDetail {
  return {
    task_id: "task-1",
    goal: "test",
    issuer_domain: "hecate",
    workspace_id: "ws-1",
    lifecycle_state: "running",
    revision: 7,
    created_at: null,
    runs: [],
    ...overrides,
  };
}

const receipt = {
  command_id: "server-id",
  kind: "provide_input",
  state: "applied",
  issued_at: "now",
  issuer: "user",
  task_ref: { kind: "task", issuer_domain: "hecate", id: "task-1" },
  detail: "live input received; it binds at the next not-yet-started decision boundary",
  delivery: {
    source_ref: "server-id",
    revision_id: "revision-1",
    run_ref: { kind: "run", issuer_domain: "hecate", id: "run-1" },
    status: "received",
  },
};

async function submitContent(text = '{"region":"EU"}') {
  fireEvent.change(screen.getByTestId("task-live-input-textarea"), { target: { value: text } });
  await userEvent.click(screen.getByTestId("task-live-input-parse"));
  await userEvent.click(screen.getByTestId("task-live-input-confirm-accept"));
}

import { fireEvent } from "@testing-library/react";

beforeEach(() => {
  apiMock.post.mockReset();
});

describe("TaskLiveInputPanel", () => {
  it("renders nothing when the task carries no live-input surface", () => {
    const { container } = render(<TaskLiveInputPanel taskId="task-1" detail={detail()} />);
    expect(container).toBeEmptyDOMElement();
    expect(apiMock.post).not.toHaveBeenCalled();
  });

  it("renders the server's unsupported reason without a composer", () => {
    render(
      <TaskLiveInputPanel
        taskId="task-1"
        detail={detail({ live_input: { supported: false, reason: "managed runner hosts do not accept live input" } })}
      />,
    );
    expect(screen.getByTestId("task-live-input-unsupported")).toHaveTextContent("managed runner");
    expect(screen.queryByTestId("task-live-input-textarea")).not.toBeInTheDocument();
    expect(apiMock.post).not.toHaveBeenCalled();
  });

  it("posts the versioned envelope with a stable command identity", async () => {
    apiMock.post.mockResolvedValueOnce(receipt);
    const onIssued = vi.fn();
    render(
      <TaskLiveInputPanel
        taskId="task-1"
        detail={detail({ live_input: { supported: true, reason: null } })}
        onIssued={onIssued}
      />,
    );
    await submitContent();
    await screen.findByTestId("task-live-input-receipt");
    expect(apiMock.post).toHaveBeenCalledTimes(1);
    const [url, body] = apiMock.post.mock.calls[0];
    expect(url).toBe("/api/tasks/task-1/commands");
    expect(body.kind).toBe("provide_input");
    expect(body.payload_schema_ref).toBe("urn:hecate:task-live-input/1");
    expect(body.payload).toEqual({ schema_version: 1, content: { region: "EU" } });
    expect(body.expected_revision).toBe(7);
    expect(typeof body.command_id).toBe("string");
    expect(onIssued).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("task-live-input-receipt-state")).toHaveTextContent("已接收");
    expect(screen.getByTestId("task-live-input-receipt")).not.toHaveTextContent("已应用");
  });

  it.each([
    new Error("network lost"),
    { error: { code: "TASK_CONTROL_ERROR", message: "live input delivery is unavailable" } },
  ])("retries an unknown result with the same command id and body (%o)", async (error) => {
    apiMock.post.mockRejectedValueOnce(error).mockResolvedValueOnce(receipt);
    render(
      <TaskLiveInputPanel taskId="task-1" detail={detail({ live_input: { supported: true, reason: null } })} />,
    );
    await submitContent();
    await screen.findByTestId("task-live-input-failure");
    const first = apiMock.post.mock.calls[0][1];
    await userEvent.click(screen.getByTestId("task-live-input-retry"));
    await screen.findByTestId("task-live-input-receipt");
    expect(apiMock.post).toHaveBeenCalledTimes(2);
    expect(apiMock.post.mock.calls[1][1]).toEqual(first);
  });

  it("blocks a confirmed operation whose revision moved on after a refresh", async () => {
    const view = render(
      <TaskLiveInputPanel taskId="task-1" detail={detail({ live_input: { supported: true, reason: null } })} />,
    );
    fireEvent.change(screen.getByTestId("task-live-input-textarea"), { target: { value: '{"region":"EU"}' } });
    await userEvent.click(screen.getByTestId("task-live-input-parse"));
    // A refresh advances the task revision before the user confirms.
    view.rerender(
      <TaskLiveInputPanel
        taskId="task-1"
        detail={detail({ revision: 9, live_input: { supported: true, reason: null } })}
      />,
    );
    await userEvent.click(screen.getByTestId("task-live-input-confirm-accept"));
    expect(screen.getByTestId("task-live-input-parse-error")).toHaveTextContent("任务状态已变化");
    expect(apiMock.post).not.toHaveBeenCalled();
  });

  it("resolves an unknown receipt even when the refresh moved the task on", async () => {
    apiMock.post.mockRejectedValueOnce(new Error("network lost")).mockResolvedValueOnce(receipt);
    const view = render(
      <TaskLiveInputPanel taskId="task-1" detail={detail({ live_input: { supported: true, reason: null } })} />,
    );
    await submitContent();
    await screen.findByTestId("task-live-input-failure");
    const first = apiMock.post.mock.calls[0][1];
    view.rerender(
      <TaskLiveInputPanel
        taskId="task-1"
        detail={detail({ revision: 9, live_input: { supported: true, reason: null } })}
      />,
    );
    await userEvent.click(screen.getByTestId("task-live-input-retry"));
    await screen.findByTestId("task-live-input-receipt");
    expect(apiMock.post.mock.calls[1][1]).toEqual(first);
  });

  it("rejects control-key content locally before any request", async () => {
    render(
      <TaskLiveInputPanel taskId="task-1" detail={detail({ live_input: { supported: true, reason: null } })} />,
    );
    fireEvent.change(screen.getByTestId("task-live-input-textarea"), {
      target: { value: '{"approval_ref":"a-1"}' },
    });
    await userEvent.click(screen.getByTestId("task-live-input-parse"));
    expect(screen.getByTestId("task-live-input-parse-error")).toHaveTextContent("审批");
    expect(screen.queryByTestId("task-live-input-confirm-accept")).not.toBeInTheDocument();
    expect(apiMock.post).not.toHaveBeenCalled();
  });
});
