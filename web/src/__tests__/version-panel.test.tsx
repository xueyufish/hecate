// Tests for the agent version panel + channel manager (1.3.20).

import { render, screen, waitFor } from "@testing-library/react";
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
