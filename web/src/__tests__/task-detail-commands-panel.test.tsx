// Task detail page assembly: the command-receipt panel is Task-scoped (works
// without any Run, outside the Run switcher), an applied command never
// rewrites the Task lifecycle presentation, and panel errors leave the
// actions/events areas untouched.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

const apiMock = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("@/lib/api-client", () => ({ api: apiMock }));
vi.mock("next/navigation", () => ({ useParams: () => ({ id: "task-1" }) }));
vi.mock("next/link", () => ({
  default: ({ children }: { children: ReactNode }) => <a>{children}</a>,
}));

import TaskDetailPage from "../app/(dashboard)/ops-center/tasks/[id]/page";
import type { TaskCommandItem, TaskCommandsPage } from "@/lib/task-command-types";

function detail(runs: Array<Record<string, unknown>>) {
  return {
    task_id: "task-1",
    goal: "核对库存并写回",
    issuer_domain: "platform",
    workspace_id: "ws-1",
    lifecycle_state: "running",
    created_at: "2026-10-18T00:00:00+00:00",
    runs,
  };
}

function run(index: number): Record<string, unknown> {
  return {
    run_id: `run-${index}`,
    attempt_no: index,
    deployment_id: "deploy-1",
    backend_ref: {},
    projection: {},
    event_cursor: null,
  };
}

function command(overrides: Partial<TaskCommandItem>): TaskCommandItem {
  return {
    command_id: "cmd-1",
    kind: "cancel",
    state: "applied",
    issued_at: "2026-10-18T00:00:00+00:00",
    issuer: "user-1",
    task_ref: { kind: "task", issuer_domain: "platform", id: "task-1" },
    run_ref: { kind: "run", issuer_domain: "runner-1", id: "run-1" },
    expires_at: null,
    expected_revision: null,
    ...overrides,
  };
}

function commandsPage(items: TaskCommandItem[]): TaskCommandsPage {
  return { items, total: items.length };
}

function actionsResponse() {
  return {
    run_id: "run-1",
    task_id: "task-1",
    actions: [
      {
        action_key: "action-1",
        execution_id: null,
        tool_call_id: null,
        tool_name: "biz_tool",
        arguments_digest: "d",
        side_effect_class: "non_idempotent_write",
        state: "claimed",
        outcome: null,
        result_digest: null,
        pending_reconciliation: false,
      },
    ],
    ledger_source: "core",
    supported: true,
  };
}

function eventsResponse() {
  return { events: [], next_cursor: null, has_more: false };
}

function routeApi(commands: () => unknown) {
  apiMock.get.mockImplementation((path: string) => {
    if (path === "/api/tasks/task-1") return Promise.resolve(detail([run(1)]));
    if (path.startsWith("/api/tasks/task-1/commands")) return commands();
    if (path === "/api/runs/run-1/actions") return Promise.resolve(actionsResponse());
    if (path.startsWith("/api/runs/run-1/events")) return Promise.resolve(eventsResponse());
    return Promise.reject(new Error(`unexpected path: ${path}`));
  });
}

beforeEach(() => {
  apiMock.get.mockReset();
});

describe("TaskDetailPage command receipts panel", () => {
  it("mounts the Task-scoped panel without any Run", async () => {
    apiMock.get.mockImplementation((path: string) => {
      if (path === "/api/tasks/task-1") return Promise.resolve(detail([]));
      if (path.startsWith("/api/tasks/task-1/commands")) {
        return Promise.resolve(commandsPage([command({ run_ref: null })]));
      }
      return Promise.reject(new Error(`unexpected path: ${path}`));
    });
    render(<TaskDetailPage />);

    // The panel renders for a Run-less Task; actions/events stay "no Run".
    expect(await screen.findByTestId("task-commands-section")).toBeInTheDocument();
    expect(await screen.findByTestId("task-command-state")).toHaveTextContent("已应用");
    await userEvent.click(screen.getByTestId("task-command-expand"));
    expect(await screen.findByTestId("task-command-detail")).toBeInTheDocument();
    expect(screen.getByText("无 Run 引用")).toBeInTheDocument();
    expect(screen.getByTestId("actions-no-run")).toBeInTheDocument();
    expect(screen.getByTestId("events-no-run")).toBeInTheDocument();
    // Only the list endpoint was queried.
    const commandPaths = apiMock.get.mock.calls
      .map((call) => call[0] as string)
      .filter((path) => path.includes("commands"));
    expect(commandPaths.length).toBeGreaterThan(0);
    for (const path of commandPaths) {
      expect(path).toMatch(/^\/api\/tasks\/task-1\/commands\?/);
    }
  });

  it("keeps the Task lifecycle untouched by an applied cancel command", async () => {
    routeApi(() => commandsPage([command({})]));
    render(<TaskDetailPage />);

    expect(await screen.findByTestId("task-commands-section")).toBeInTheDocument();
    // The lifecycle badge keeps showing the server-recorded state.
    expect(await screen.findByTestId("task-command-state")).toHaveTextContent("已应用");
    expect(screen.getByTestId("task-summary")).toHaveTextContent("执行中");
  });

  it("isolates a commands query failure from actions and events panels", async () => {
    routeApi(() => Promise.reject({ detail: { error: { code: "DB_DOWN", message: "query failed" } } }));
    render(<TaskDetailPage />);

    expect(await screen.findByTestId("task-commands-error")).toHaveTextContent("DB_DOWN");
    // Actions and events panels still render their normal content.
    expect(await screen.findByTestId("action-row")).toBeInTheDocument();
    expect(await screen.findByTestId("run-events-empty")).toBeInTheDocument();
  });
});

it("preserves the command page and expansion when the selected Run changes", async () => {
  const firstPage = Array.from({ length: 20 }, (_, index) => command({ command_id: `cmd-${index}` }));
  apiMock.get.mockImplementation((path: string) => {
    if (path === "/api/tasks/task-1") return Promise.resolve(detail([run(1), run(2)]));
    if (path === "/api/tasks/task-1/commands?limit=20&offset=0") {
      return Promise.resolve({ items: firstPage, total: 21 });
    }
    if (path === "/api/tasks/task-1/commands?limit=20&offset=20") {
      return Promise.resolve(commandsPage([command({ command_id: "cmd-last", issuer: "last-issuer" })]));
    }
    if (/^\/api\/runs\/run-[12]\/actions$/.test(path)) return Promise.resolve(actionsResponse());
    if (/^\/api\/runs\/run-[12]\/events/.test(path)) return Promise.resolve(eventsResponse());
    throw new Error(`Unexpected request: ${path}`);
  });
  render(<TaskDetailPage />);
  await screen.findAllByTestId("task-command-row");
  await userEvent.click(within(screen.getByTestId("task-commands-panel")).getByRole("button", { name: "下一页" }));
  await screen.findByText("last-issuer");
  await userEvent.click(screen.getByTestId("task-command-expand"));
  expect(screen.getByTestId("task-command-detail")).toHaveTextContent("cmd-last");

  await userEvent.click(screen.getByRole("button", { name: "Run #1" }));
  await waitFor(() => expect(apiMock.get).toHaveBeenCalledWith("/api/runs/run-1/actions"));
  expect(screen.getByRole("button", { name: "Run #1" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByTestId("task-command-detail")).toHaveTextContent("cmd-last");
  expect(apiMock.get.mock.calls.map(([path]) => path).filter((path) => path.includes("/commands?")))
    .toEqual([
      "/api/tasks/task-1/commands?limit=20&offset=0",
      "/api/tasks/task-1/commands?limit=20&offset=20",
    ]);
});
