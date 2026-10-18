// TaskCommandsPanel behavior: Task-scoped list queries, verbatim receipt
// states without local-time rewriting, explicit missing values, offset
// pagination with a way back from failed later pages, stale-response
// rejection across Task switching, and list-GET-only network traffic.

import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TaskCommandsPanel } from "@/components/tasks/task-commands-panel";
import type { TaskCommandItem, TaskCommandsPage } from "@/lib/task-command-types";

const apiMock = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("@/lib/api-client", () => ({ api: apiMock }));

function command(
  overrides: Partial<TaskCommandItem> & { command_id?: string },
): TaskCommandItem {
  const id = overrides.command_id ?? `cmd-${Math.random().toString(36).slice(2, 8)}`;
  return {
    command_id: id,
    kind: "cancel",
    state: "requested",
    issued_at: "2026-10-18T00:00:00+00:00",
    issuer: "issuer-" + id.split("-")[0],
    task_ref: { kind: "task", issuer_domain: "platform", id: "task-1" },
    run_ref: { kind: "run", issuer_domain: "runner-1", id: "run-9" },
    expires_at: null,
    expected_revision: null,
    ...overrides,
  };
}

function page(items: TaskCommandItem[], total = items.length): TaskCommandsPage {
  return { items, total };
}

function fullPage(prefix: string, count = 20): TaskCommandItem[] {
  return Array.from({ length: count }, (_, index) =>
    command({ command_id: `${prefix}-${index}`, issuer: `issuer-${prefix}` }));
}

function url(taskId: string, offset: number) {
  return `/api/tasks/${taskId}/commands?limit=20&offset=${offset}`;
}

async function expandFirstRow() {
  await userEvent.click(screen.getAllByTestId("task-command-expand")[0]);
}

beforeEach(() => {
  apiMock.get.mockReset();
});

describe("TaskCommandsPanel", () => {
  it("renders command fields and expandable details from the first page", async () => {
    apiMock.get.mockResolvedValueOnce(page([
      command({
        command_id: "cmd-1", kind: "provide_input", state: "acknowledged",
        issuer: "user-1",
        run_ref: null, expires_at: "2026-10-19T00:00:00+00:00", expected_revision: 3,
      }),
    ]));
    render(<TaskCommandsPanel taskId="task-1" />);

    expect(await screen.findByTestId("task-commands-table")).toBeInTheDocument();
    expect(screen.getByText("补充输入")).toBeInTheDocument();
    expect(screen.getByText("已确认接收")).toBeInTheDocument();
    expect(screen.getByText("user-1")).toBeInTheDocument();
    expect(apiMock.get).toHaveBeenCalledWith(url("task-1", 0));

    await expandFirstRow();
    expect(screen.getByTestId("task-command-detail")).toHaveTextContent("cmd-1");
    expect(screen.getByTestId("task-command-detail")).toHaveTextContent("platform/task-1");
    expect(screen.getByTestId("task-command-run-ref")).toHaveTextContent("无 Run 引用");
    expect(screen.getByTestId("task-command-expires")).not.toHaveTextContent("未记录");
    expect(screen.getByTestId("task-command-revision")).toHaveTextContent("3");
    // A run reference is display text, never a navigation link.
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("renders the five recorded states verbatim and falls back for unknown ones", async () => {
    apiMock.get.mockResolvedValueOnce(page([
      command({ command_id: "s-1", state: "requested" }),
      command({ command_id: "s-2", state: "acknowledged" }),
      command({ command_id: "s-3", state: "applied" }),
      command({ command_id: "s-4", state: "rejected" }),
      command({ command_id: "s-5", state: "expired" }),
      command({ command_id: "s-6", state: "future_state" }),
    ]));
    render(<TaskCommandsPanel taskId="task-1" />);

    await screen.findByTestId("task-commands-table");
    expect(screen.getAllByTestId("task-command-state").map((el) => el.textContent)).toEqual([
      "已请求", "已确认接收", "已应用", "已拒绝", "已过期", "future_state",
    ]);
  });

  it("never derives expiry from local time", async () => {
    apiMock.get.mockResolvedValueOnce(page([
      command({ command_id: "old-1", state: "requested", expires_at: "2000-01-01T00:00:00+00:00" }),
    ]));
    render(<TaskCommandsPanel taskId="task-1" />);

    await screen.findByTestId("task-commands-table");
    expect(screen.getByTestId("task-command-state")).toHaveTextContent("已请求");
    await expandFirstRow();
    // The recorded expiry stays visible as a fact; no state rewriting occurs.
    expect(screen.getByTestId("task-command-expires")).not.toHaveTextContent("未记录");
    expect(screen.getByTestId("task-command-state")).toHaveTextContent("已请求");
  });

  it("marks missing optional fields explicitly", async () => {
    apiMock.get.mockResolvedValueOnce(page([
      command({ command_id: "m-1", run_ref: null, expires_at: null, expected_revision: null }),
    ]));
    render(<TaskCommandsPanel taskId="task-1" />);

    await screen.findByTestId("task-commands-table");
    await expandFirstRow();
    expect(screen.getByTestId("task-command-run-ref")).toHaveTextContent("无 Run 引用");
    expect(screen.getByTestId("task-command-expires")).toHaveTextContent("未记录");
    expect(screen.getByTestId("task-command-revision")).toHaveTextContent("未记录");
  });

  it("shows the empty state without completion wording", async () => {
    apiMock.get.mockResolvedValueOnce(page([], 0));
    render(<TaskCommandsPanel taskId="task-1" />);

    expect(await screen.findByTestId("task-commands-empty")).toHaveTextContent("暂无命令");
    expect(screen.queryByText(/已完成|执行成功/)).not.toBeInTheDocument();
  });

  it("shows commands across runs without Run filtering", async () => {
    apiMock.get.mockResolvedValueOnce(page([
      command({ command_id: "r-1", run_ref: { kind: "run", issuer_domain: "d1", id: "run-9" } }),
      command({ command_id: "r-2", run_ref: { kind: "run", issuer_domain: "d2", id: "run-7" } }),
      command({ command_id: "r-3", run_ref: null }),
    ]));
    render(<TaskCommandsPanel taskId="task-1" />);

    await screen.findByTestId("task-commands-table");
    expect(screen.getAllByTestId("task-command-row")).toHaveLength(3);
  });
});

describe("TaskCommandsPanel pagination", () => {
  it("pages forward by offset and back through the offset stack", async () => {
    apiMock.get
      .mockResolvedValueOnce(page(fullPage("p1")))
      .mockResolvedValueOnce(page(fullPage("p2", 1)))
      .mockResolvedValueOnce(page(fullPage("p1")));
    render(<TaskCommandsPanel taskId="task-1" />);
    await screen.findAllByText("issuer-p1");

    await userEvent.click(screen.getByRole("button", { name: "下一页" }));
    expect((await screen.findAllByText("issuer-p2")).length).toBeGreaterThan(0);
    expect(apiMock.get).toHaveBeenLastCalledWith(url("task-1", 20));
    // A short page is the last one; the note stays query-only.
    expect(screen.getByTestId("task-commands-end")).toHaveTextContent("已是最后一页");
    expect(screen.getByRole("button", { name: "下一页" })).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: "上一页" }));
    expect((await screen.findAllByText("issuer-p1")).length).toBeGreaterThan(0);
    expect(apiMock.get).toHaveBeenLastCalledWith(url("task-1", 0));
  });

  it("keeps previous-page navigation when a later page is empty", async () => {
    apiMock.get
      .mockResolvedValueOnce(page(fullPage("p1")))
      .mockResolvedValueOnce(page([], 20))
      .mockResolvedValueOnce(page(fullPage("p1")));
    render(<TaskCommandsPanel taskId="task-1" />);
    await screen.findAllByText("issuer-p1");

    await userEvent.click(screen.getByRole("button", { name: "下一页" }));
    expect(await screen.findByTestId("task-commands-empty")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "上一页" })).toBeEnabled();

    await userEvent.click(screen.getByRole("button", { name: "上一页" }));
    expect((await screen.findAllByText("issuer-p1")).length).toBeGreaterThan(0);
    expect(apiMock.get).toHaveBeenLastCalledWith(url("task-1", 0));
  });

  it("refresh reloads the current page instead of resetting to the first", async () => {
    apiMock.get
      .mockResolvedValueOnce(page(fullPage("p1")))
      .mockResolvedValueOnce(page(fullPage("p2", 3)))
      .mockResolvedValueOnce(page(fullPage("p2", 3)));
    render(<TaskCommandsPanel taskId="task-1" />);
    await screen.findAllByText("issuer-p1");
    await userEvent.click(screen.getByRole("button", { name: "下一页" }));
    await screen.findAllByText("issuer-p2");

    await userEvent.click(screen.getByRole("button", { name: "刷新" }));
    expect((await screen.findAllByText("issuer-p2")).length).toBeGreaterThan(0);
    expect(apiMock.get).toHaveBeenLastCalledWith(url("task-1", 20));
  });

  it("shows errors with code and message, keeps the way back, retries in place", async () => {
    apiMock.get
      .mockResolvedValueOnce(page(fullPage("p1")))
      .mockRejectedValueOnce({ detail: { error: { code: "TIMEOUT", message: "page failed" } } })
      .mockResolvedValueOnce(page(fullPage("p2", 2)));
    render(<TaskCommandsPanel taskId="task-1" />);
    await screen.findAllByText("issuer-p1");

    await userEvent.click(screen.getByRole("button", { name: "下一页" }));
    expect(await screen.findByTestId("task-commands-error")).toHaveTextContent("TIMEOUT");
    expect(screen.getByTestId("task-commands-error")).toHaveTextContent("page failed");
    // A failed later page still offers the way back.
    expect(screen.getByRole("button", { name: "上一页" })).toBeEnabled();

    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(apiMock.get).toHaveBeenLastCalledWith(url("task-1", 20));
    expect((await screen.findAllByText("issuer-p2")).length).toBeGreaterThan(0);
  });
});

describe("TaskCommandsPanel task switching", () => {
  it.each(["success", "error"] as const)("discards a stale page-2 %s after A -> B -> A", async (outcome) => {
    let resolveOld!: (value: object) => void;
    let rejectOld!: (reason: unknown) => void;
    const old = new Promise<object>((resolve, reject) => { resolveOld = resolve; rejectOld = reject; });
    apiMock.get
      .mockResolvedValueOnce(page(fullPage("a1")))
      .mockReturnValueOnce(old)
      .mockResolvedValueOnce(page(fullPage("b1")))
      .mockResolvedValueOnce(page(fullPage("a2")));
    const view = render(<TaskCommandsPanel taskId="task-A" />);
    await screen.findAllByText("issuer-a1");
    await userEvent.click(screen.getByRole("button", { name: "下一页" }));
    expect(screen.getByTestId("task-commands-loading")).toBeInTheDocument();

    view.rerender(<TaskCommandsPanel taskId="task-B" />);
    expect((await screen.findAllByText("issuer-b1")).length).toBeGreaterThan(0);
    view.rerender(<TaskCommandsPanel taskId="task-A" />);
    expect((await screen.findAllByText("issuer-a2")).length).toBeGreaterThan(0);
    expect(apiMock.get).toHaveBeenLastCalledWith(url("task-A", 0));
    expect(screen.getByRole("button", { name: "上一页" })).toBeDisabled();

    await act(async () => {
      if (outcome === "success") resolveOld(page(fullPage("stale")));
      else rejectOld({ detail: { error: { code: "LATE", message: "stale failure" } } });
    });
    expect(screen.getAllByText("issuer-a2").length).toBeGreaterThan(0);
    expect(screen.queryByText("issuer-stale")).not.toBeInTheDocument();
    expect(screen.queryByTestId("task-commands-error")).not.toBeInTheDocument();
  });

  it("resets pagination and expansion when switching Task", async () => {
    apiMock.get
      .mockResolvedValueOnce(page(fullPage("a1")))
      .mockResolvedValueOnce(page(fullPage("b1")))
      .mockResolvedValueOnce(page(fullPage("a1")));
    const view = render(<TaskCommandsPanel taskId="task-A" />);
    await screen.findAllByText("issuer-a1");
    await expandFirstRow();
    expect(screen.getByTestId("task-command-detail")).toHaveTextContent("a1-0");

    view.rerender(<TaskCommandsPanel taskId="task-B" />);
    expect((await screen.findAllByText("issuer-b1")).length).toBeGreaterThan(0);
    expect(screen.queryByTestId("task-command-detail")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "上一页" })).toBeDisabled();

    // Switching back issues a fresh first-page request for A.
    view.rerender(<TaskCommandsPanel taskId="task-A" />);
    expect((await screen.findAllByText("issuer-a1")).length).toBeGreaterThan(0);
    expect(apiMock.get).toHaveBeenLastCalledWith(url("task-A", 0));
  });
});

it("issues only list queries across browsing, expansion and refresh", async () => {
  apiMock.get
    .mockResolvedValue(page(fullPage("p1")));
  render(<TaskCommandsPanel taskId="task-1" />);
  await screen.findAllByText("issuer-p1");
  await expandFirstRow();
  await userEvent.click(screen.getByRole("button", { name: "刷新" }));
  await screen.findAllByText("issuer-p1");

  const paths = apiMock.get.mock.calls.map((call) => call[0] as string);
  expect(paths.length).toBeGreaterThan(0);
  for (const path of paths) {
    expect(path).toMatch(/^\/api\/tasks\/task-1\/commands\?limit=20&offset=\d+$/);
    expect(path).not.toContain("/api/commands/");
  }
});

it("renders prototype property names as raw unknown receipt values", async () => {
  apiMock.get.mockResolvedValueOnce(page([
    command({ command_id: "proto-1", state: "constructor", kind: "__proto__" }),
    command({ command_id: "proto-2", state: "toString", kind: "hasOwnProperty" }),
  ]));
  render(<TaskCommandsPanel taskId="task-1" />);
  await screen.findByTestId("task-commands-table");
  expect(screen.getAllByTestId("task-command-state").map((el) => el.textContent))
    .toEqual(["constructor", "toString"]);
  expect(screen.getByText("__proto__")).toBeInTheDocument();
  expect(screen.getByText("hasOwnProperty")).toBeInTheDocument();
});

it("returns from a failed later page without requiring a successful retry", async () => {
  apiMock.get
    .mockResolvedValueOnce(page(fullPage("p1")))
    .mockRejectedValueOnce({ detail: { error: { code: "TIMEOUT", message: "page failed" } } })
    .mockResolvedValueOnce(page(fullPage("p1")));
  render(<TaskCommandsPanel taskId="task-1" />);
  await screen.findAllByText("issuer-p1");
  await userEvent.click(screen.getByRole("button", { name: "下一页" }));
  await screen.findByTestId("task-commands-error");
  await userEvent.click(screen.getByRole("button", { name: "上一页" }));
  await screen.findAllByText("issuer-p1");
  expect(apiMock.get).toHaveBeenLastCalledWith(url("task-1", 0));
  expect(screen.queryByTestId("task-commands-error")).not.toBeInTheDocument();
});
