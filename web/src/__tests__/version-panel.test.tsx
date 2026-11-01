// Tests for the agent version panel + channel manager (1.3.20).

import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ChannelManager } from "@/components/agent/channel-manager";
import { VersionPanel } from "@/components/agent/version-panel";

const mockVersion = vi.hoisted(() => ({
  api: {
    list: vi.fn(),
    get: vi.fn(),
    status: vi.fn(),
    commit: vi.fn(),
    publish: vi.fn(),
    rollback: vi.fn(),
    diff: vi.fn(),
    drift: vi.fn(),
    rename: vi.fn(),
    remove: vi.fn(),
  },
  channels: {
    list: vi.fn(),
    get: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    remove: vi.fn(),
  },
}));

vi.mock("@/lib/api-client", () => ({
  agentVersionsApi: mockVersion.api,
  channelsApi: mockVersion.channels,
}));

describe("VersionPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows 未提交 badge when the agent has no versions", async () => {
    mockVersion.api.status.mockResolvedValue({
      agent_id: "a1",
      latest_version: null,
      published_version: null,
      has_uncommitted_changes: true,
    });
    mockVersion.api.list.mockResolvedValue({ items: [], total: 0 });

    render(<VersionPanel agentId="a1" />);

    await waitFor(() => expect(screen.getByText("未提交")).toBeInTheDocument());
    expect(screen.getByText(/尚无版本/)).toBeInTheDocument();
  });

  it("shows 已发布 badge and version rows when published", async () => {
    mockVersion.api.status.mockResolvedValue({
      agent_id: "a1",
      latest_version: 2,
      published_version: 1,
      has_uncommitted_changes: false,
    });
    mockVersion.api.list.mockResolvedValue({
      items: [
        {
          id: "v2",
          agent_id: "a1",
          version: 2,
          name: "",
          change_summary: "",
          content_hash: "h2",
          is_published: false,
          created_at: "2026-09-14T00:00:00Z",
        },
        {
          id: "v1",
          agent_id: "a1",
          version: 1,
          name: "first",
          change_summary: "initial",
          content_hash: "h1",
          is_published: true,
          created_at: "2026-09-13T00:00:00Z",
        },
      ],
      total: 2,
    });

    render(<VersionPanel agentId="a1" />);

    await waitFor(() => expect(screen.getByText("已发布 v1")).toBeInTheDocument());
    expect(screen.getByText("first")).toBeInTheDocument();
    expect(screen.queryByText("有未提交变更")).not.toBeInTheDocument();
  });

  it("commits a version and refreshes", async () => {
    const user = (await import("@testing-library/user-event")).default;
    mockVersion.api.status.mockResolvedValue({
      agent_id: "a1",
      latest_version: 1,
      published_version: null,
      has_uncommitted_changes: true,
    });
    mockVersion.api.list.mockResolvedValue({ items: [], total: 0 });
    mockVersion.api.commit.mockResolvedValue({});

    render(<VersionPanel agentId="a1" />);
    await waitFor(() => expect(screen.getByText("提交版本")).toBeInTheDocument());

    await user.click(screen.getByText("提交版本"));
    await user.click(screen.getByText("确认提交"));

    await waitFor(() =>
      expect(mockVersion.api.commit).toHaveBeenCalledWith("a1", {
        name: undefined,
        change_summary: undefined,
      })
    );
  });

  const statusReady = { agent_id: "a1", latest_version: 2, published_version: null, has_uncommitted_changes: false };
  const versionRows = [
    { id: "v2", version: 2, name: "second", change_summary: "", is_published: false },
    { id: "v1", version: 1, name: "first", change_summary: "", is_published: false },
  ];

  function driftReport(toolContracts?: unknown, drifted: unknown[] = []) {
    return { agent_id: "a1", version: 1, drifted, ...(toolContracts ? { tool_contracts: toolContracts } : {}) };
  }

  async function openDrift(report: unknown) {
    const user = (await import("@testing-library/user-event")).default;
    mockVersion.api.status.mockResolvedValue(statusReady);
    mockVersion.api.list.mockResolvedValue({ items: versionRows, total: 2 });
    mockVersion.api.drift.mockResolvedValue(report);
    const view = render(<VersionPanel agentId="a1" />);
    await waitFor(() => expect(screen.getByText("v1")).toBeInTheDocument());
    await user.click(screen.getAllByRole("button", { name: "漂移" })[1]);
    await waitFor(() => expect(screen.getByTestId("drift-report")).toBeInTheDocument());
    return { user, view };
  }

  it("refuses an all-consistent verdict while any tool is unverified", async () => {
    await openDrift(
      driftReport({
        format_version: 1,
        digest_scope: ["name", "description", "parameters"],
        tools: [
          { name: "web_search", status: "consistent" },
          { name: "legacy_tool", status: "unverified", detail: "snapshot predates tool contract capture" },
        ],
        summary: { total: 2, consistent: 1, changed: 0, missing: 0, unverified: 1 },
      }),
    );
    expect(screen.getByTestId("tool-contract-summary").textContent).toContain("未核验 1");
    expect(screen.getByTestId("tool-contract-problem-row").textContent).toContain("legacy_tool");
    expect(screen.getByTestId("tool-contract-not-verified").textContent).toContain("未核验不等于一致");
    expect(screen.queryByTestId("tool-contract-all-verified")).not.toBeInTheDocument();
    expect(screen.getByTestId("tool-contract-scope").textContent).toContain("未发起外部调用");
  });

  it("shows the all-verified verdict only when every tool verified consistent", async () => {
    await openDrift(
      driftReport({
        format_version: 1,
        digest_scope: ["name", "description", "parameters"],
        tools: [{ name: "web_search", status: "consistent" }],
        summary: { total: 1, consistent: 1, changed: 0, missing: 0, unverified: 0 },
      }),
    );
    expect(screen.getByTestId("tool-contract-all-verified").textContent).toContain("已核验工具契约全部一致");
    expect(screen.queryByTestId("tool-contract-not-verified")).not.toBeInTheDocument();
  });

  it("renders hash movement and a binding change without claiming drift where hashes match", async () => {
    await openDrift(
      driftReport({
        format_version: 1,
        digest_scope: ["name", "description", "parameters"],
        tools: [
          {
            name: "rebuilt_tool",
            status: "consistent",
            binding_changed: true,
            detail: "resolved to a different database row than the committed binding (same-name rebuild)",
          },
          { name: "changed_tool", status: "changed", committed_contract_hash: "a".repeat(16) + "…", live_contract_hash: "b".repeat(16) + "…" },
        ],
        summary: { total: 2, consistent: 1, changed: 1, missing: 0, unverified: 0 },
      }),
    );
    const rows = screen.getAllByTestId("tool-contract-problem-row");
    expect(rows[0].textContent).toContain("解析来源或绑定已变化");
    expect(rows[0].textContent).not.toContain("→");
    expect(rows[1].textContent).toContain("契约变化");
    expect(rows[1].textContent).toContain("→");
  });

  it("treats a report without tool contracts as unverified, not as consistent", async () => {
    await openDrift(driftReport());
    expect(screen.getByTestId("tool-contract-absent").textContent).toContain("工具契约一律视为未核验");
    expect(screen.queryByTestId("tool-contract-all-verified")).not.toBeInTheDocument();
  });

  it("drops a late drift response that belongs to a previous version", async () => {
    const user = (await import("@testing-library/user-event")).default;
    mockVersion.api.status.mockResolvedValue(statusReady);
    mockVersion.api.list.mockResolvedValue({ items: versionRows, total: 2 });
    let resolveSlow: (report: unknown) => void = () => {};
    const slow = new Promise((resolve) => {
      resolveSlow = resolve;
    });
    mockVersion.api.drift.mockImplementation((_agent: string, version: number) =>
      version === 2 ? slow : Promise.resolve(driftReport({
        format_version: 1,
        digest_scope: ["name", "description", "parameters"],
        tools: [{ name: "quick_tool", status: "changed", committed_contract_hash: "a", live_contract_hash: "b" }],
        summary: { total: 1, consistent: 0, changed: 1, missing: 0, unverified: 0 },
      })),
    );
    render(<VersionPanel agentId="a1" />);
    await waitFor(() => expect(screen.getByText("v2")).toBeInTheDocument());
    const buttons = screen.getAllByRole("button", { name: "漂移" });
    await user.click(buttons[0]); // v2 — slow response hangs in flight
    await user.click(buttons[1]); // v1 — resolves immediately
    await waitFor(() => expect(screen.getByTestId("drift-report")).toBeInTheDocument());
    expect(screen.getByTestId("drift-report-version").textContent).toContain("v1");
    await act(async () => {
      resolveSlow(driftReport({
        format_version: 1,
        digest_scope: ["name", "description", "parameters"],
        tools: [{ name: "stale_tool", status: "missing" }],
        summary: { total: 1, consistent: 0, changed: 0, missing: 1, unverified: 0 },
      }));
    });
    // The stale v2 report never replaces the v1 view the user holds.
    expect(screen.getByTestId("drift-report-version").textContent).toContain("v1");
    const report = screen.getByTestId("drift-report");
    expect(report.textContent).toContain("quick_tool");
    expect(report.textContent).not.toContain("stale_tool");
  });

  it("clears the drift view when the panel switches to another agent", async () => {
    const { view } = await openDrift(
      driftReport({
        format_version: 1,
        digest_scope: ["name", "description", "parameters"],
        tools: [{ name: "web_search", status: "consistent" }],
        summary: { total: 1, consistent: 1, changed: 0, missing: 0, unverified: 0 },
      }),
    );
    view.rerender(<VersionPanel agentId="a2" />);
    expect(screen.queryByTestId("drift-report")).not.toBeInTheDocument();
  });

  it.each(["resolve", "reject"])("drops a pending previous-agent drift response (%s)", async (outcome) => {
    const user = (await import("@testing-library/user-event")).default;
    mockVersion.api.status.mockResolvedValue(statusReady);
    mockVersion.api.list.mockResolvedValue({ items: versionRows, total: 2 });
    let resolveSlow: (report: unknown) => void = () => {};
    let rejectSlow: (reason: Error) => void = () => {};
    mockVersion.api.drift.mockImplementation(() => new Promise((resolve, reject) => {
      resolveSlow = resolve;
      rejectSlow = reject;
    }));
    const view = render(<VersionPanel agentId="a1" />);
    await screen.findByText("v1");
    await user.click(screen.getAllByRole("button", { name: "漂移" })[1]);
    view.rerender(<VersionPanel agentId="a2" />);
    await act(async () => {
      if (outcome === "resolve") resolveSlow(driftReport());
      else rejectSlow(new Error("old agent error"));
    });
    expect(screen.queryByTestId("drift-report")).not.toBeInTheDocument();
    expect(screen.queryByText("Drift check failed")).not.toBeInTheDocument();
  });

  it("does not give an all-clear when the binding changed and the contract hash also changed", async () => {
    await openDrift(driftReport({
      format_version: 1, digest_scope: ["name", "description", "parameters"],
      tools: [{ name: "rebuilt_tool", status: "changed", binding_changed: true,
        committed_contract_hash: "a".repeat(64), live_contract_hash: "b".repeat(64) }],
      summary: { total: 1, consistent: 0, changed: 1, missing: 0, unverified: 0 },
    }));
    expect(screen.getByTestId("tool-contract-problem-row")).not.toHaveTextContent("哈希一致");
  });

  it("keeps a binding change visible without an unconditional all-clear", async () => {
    await openDrift(driftReport({
      format_version: 1, digest_scope: ["name", "description", "parameters"],
      tools: [{ name: "rebuilt_tool", status: "consistent", binding_changed: true,
        committed_contract_hash: "a".repeat(64), live_contract_hash: "a".repeat(64) }],
      summary: { total: 1, consistent: 1, changed: 0, missing: 0, unverified: 0 },
    }));
    expect(screen.queryByTestId("tool-contract-all-verified")).not.toBeInTheDocument();
    expect(screen.getByTestId("tool-contract-problem-row")).toHaveTextContent("解析");
  });

  it.each([
    { format_version: 99, digest_scope: ["name", "description", "parameters"] },
    { format_version: 1, digest_scope: ["name"] },
  ])("treats unsupported diagnostics as unverified ($format_version / $digest_scope)", async (format) => {
    await openDrift(driftReport({
      ...format,
      tools: [{ name: "tool", status: "consistent" }],
      summary: { total: 1, consistent: 1, changed: 0, missing: 0, unverified: 0 },
    }));
    expect(screen.getByTestId("tool-contract-not-verified")).toHaveTextContent("不受支持");
    expect(screen.queryByTestId("tool-contract-all-verified")).not.toBeInTheDocument();
  });
});

describe("ChannelManager", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows empty state when no channels exist", async () => {
    mockVersion.channels.list.mockResolvedValue([]);

    render(<ChannelManager agentId="a1" />);

    await waitFor(() => expect(screen.getByText(/尚无渠道/)).toBeInTheDocument());
  });

  it("lists channels with bind mode and status", async () => {
    mockVersion.channels.list.mockResolvedValue([
      {
        id: "c1",
        workspace_id: "w1",
        name: "api-main",
        type: "api",
        agent_id: "a1",
        bind_mode: "published",
        pinned_version: null,
        config: {},
        status: "active",
        created_at: "2026-09-14T00:00:00Z",
      },
    ]);

    render(<ChannelManager agentId="a1" />);

    await waitFor(() => expect(screen.getByText("api-main")).toBeInTheDocument());
    expect(screen.getByText("追踪最新发布")).toBeInTheDocument();
    expect(screen.getByText("调用地址")).toBeInTheDocument();
  });
});
