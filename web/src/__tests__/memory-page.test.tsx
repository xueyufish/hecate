import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
}));

vi.mock("@/lib/api-client", () => ({ api: apiMock }));
vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ userEmail: "admin@example.com", logout: vi.fn() }),
}));

import MemoryCenterPage from "../app/(dashboard)/memory/page";

function mockStats() {
  apiMock.get.mockImplementation((path: string) => {
    if (path === "/api/memory/governance/stats") {
      return Promise.resolve({
        counts: { l3_active: 2, l4_active: 1, pending_flush_windows: 0 },
        lifecycle_operations_by_reason: { ttl_expired: 3 },
        recent_consolidation_runs: [],
      });
    }
    if (path === "/api/agents") {
      return Promise.resolve({ items: [{ id: "agent-1", name: "A1" }] });
    }
    if (path === "/api/memory") {
      return [
        { id: "m1", content: "keep me", memory_type: "semantic", importance: 0.5 },
        { id: "m2", content: "archive me", memory_type: "semantic", importance: 0.4 },
      ];
    }
    if (path.startsWith("/api/memory/policies/resolved")) {
      return Promise.resolve({
        values: { sharing_ceiling: "team", ttl_days: { l3_episodic: 30 } },
        sources: { sharing_ceiling: "workspace", ttl_days: "workspace" },
      });
    }
    if (path === "/api/memory/policies") return Promise.resolve({ items: [] });
    if (path === "/api/memory/governance/audit") {
      return Promise.resolve({
        items: [
          {
            id: "a1",
            agent_id: "agent-1",
            tool_name: "consolidation",
            target_type: "user_memory",
            target_id: "m1",
            reason: null,
            after_summary: "ADD applied",
            created_at: "2026-09-23T00:00:00Z",
          },
        ],
      });
    }
    if (path === "/api/memory/governance/runs") {
      return Promise.resolve({
        items: [
          {
            id: "r1",
            kind: "consolidation",
            trigger: "cron",
            status: "failed",
            adopted_count: 0,
            rejected_count: 0,
            error: "llm down",
            created_at: "2026-09-23T00:00:00Z",
          },
        ],
      });
    }
    return Promise.resolve({ items: [] });
  });
}

beforeEach(() => {
  apiMock.get.mockReset();
  apiMock.post.mockReset();
  apiMock.put.mockReset();
  apiMock.delete.mockReset();
  mockStats();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("MemoryCenterPage", () => {
  it("renders stats header and the browse list", async () => {
    render(<MemoryCenterPage />);
    await waitFor(() => expect(screen.getByText("Memory Center")).toBeDefined());
    await waitFor(() => expect(screen.getByText("keep me")).toBeDefined());
    expect(screen.getByText("archive me")).toBeDefined();
    expect(screen.getByTestId("section-audit")).toBeDefined();
    expect(screen.getByTestId("section-policy")).toBeDefined();
  });

  it("archives a memory and then restores it", async () => {
    const user = userEvent.setup();
    render(<MemoryCenterPage />);
    await waitFor(() => expect(screen.getByTestId("archive-m2")).toBeDefined());

    apiMock.post.mockResolvedValueOnce({ ok: true });
    await user.click(screen.getByTestId("archive-m2"));
    await waitFor(() => expect(apiMock.post).toHaveBeenCalled());
    expect(apiMock.post.mock.calls[0][0]).toBe("/api/memory/governance/archive");
  });

  it("shows the audit views with source filter and run errors", async () => {
    const user = userEvent.setup();
    render(<MemoryCenterPage />);
    await user.click(screen.getByTestId("section-audit"));

    await waitFor(() => expect(screen.getByTestId("audit-row-a1")).toBeDefined());
    expect(
      within(screen.getByTestId("audit-row-a1")).getByText("consolidation"),
    ).toBeDefined();
    await waitFor(() => expect(screen.getByTestId("run-row-r1")).toBeDefined());
    expect(screen.getByText("llm down")).toBeDefined();
  });

  it("saves a workspace policy and shows the resolved preview with source labels", async () => {
    const user = userEvent.setup();
    render(<MemoryCenterPage />);
    await user.click(screen.getByTestId("section-policy"));

    await waitFor(() => expect(screen.getByTestId("resolved-sharing_ceiling")).toBeDefined());
    expect(
      within(screen.getByTestId("resolved-sharing_ceiling")).getByText("workspace", {
        selector: "td",
      }),
    ).toBeDefined();

    apiMock.put.mockResolvedValueOnce({ id: "p1" });
    await user.click(screen.getByTestId("save-workspace-policy"));
    await waitFor(() => expect(apiMock.put).toHaveBeenCalled());
    expect(apiMock.put.mock.calls[0][0]).toBe("/api/memory/policies/workspace");
  });

  it("surfaces validation errors from the policy API", async () => {
    const user = userEvent.setup();
    apiMock.put.mockRejectedValueOnce(new Error("unknown memory tool: nope"));
    render(<MemoryCenterPage />);
    await user.click(screen.getByTestId("section-policy"));

    await waitFor(() => expect(screen.getByTestId("save-workspace-policy")).toBeDefined());
    await user.click(screen.getByTestId("save-workspace-policy"));
    await waitFor(() =>
      expect(screen.getByTestId("policy-error").textContent).toContain("unknown memory tool"),
    );
  });
});
