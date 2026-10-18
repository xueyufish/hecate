// Tests for the agent publication history panel (step11 read-only slice).

import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PublicationHistoryPanel } from "@/components/agent/publication-history-panel";
import AgentDetailPage from "@/app/(dashboard)/agents/[id]/page";
import { outcomeLabel, outcomeTone, type PublicationItem, type PublicationListResponse } from "@/lib/publication-types";

const apiMock = vi.hoisted(() => ({ get: vi.fn() }));
const route = vi.hoisted(() => ({ id: "agent-1" }));
vi.mock("@/lib/api-client", () => ({ api: apiMock }));
vi.mock("next/navigation", () => ({
  useParams: () => route,
  useRouter: () => ({ push: vi.fn() }),
}));
vi.mock("@/components/agent/agent-configurator", () => ({
  AgentConfigurator: () => <div>configurator</div>,
}));
vi.mock("@/components/agent/channel-manager", () => ({
  ChannelManager: () => <div>channels</div>,
}));
vi.mock("@/components/agent/version-panel", () => ({
  VersionPanel: () => <div>versions</div>,
}));

const AGENT_ID = "agent-1";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

let seq = 0;
function receipt(overrides: Partial<PublicationItem> = {}): PublicationItem {
  seq += 1;
  return {
    id: `receipt-${seq}`,
    agent_id: AGENT_ID,
    version: 2,
    agent_config_hash: `hash-${seq}`,
    candidate_run_id: `run-cand-${seq}`,
    candidate_run_hash: `cand-hash-${seq}`,
    baseline_run_id: null,
    baseline_run_hash: null,
    gate_config: { mode: "require", block_on_regression: true },
    gate_report: {
      mode: "require", bypassed_by_force: false, deterministic_pass_rate: 0.95,
      signals: [{ name: "deterministic_pass_rate", enabled: true, passed: true, detail: {} }],
    },
    outcome: "passed",
    operator_user_id: "user-1",
    created_at: "2026-10-18T10:00:00Z",
    ...overrides,
  };
}

function pubList(items: PublicationItem[], total = items.length): PublicationListResponse {
  return { items, total };
}

function byUrl(url: string, publications: PublicationListResponse | Error, agentBody: Record<string, unknown>) {
  if (String(url).includes("/publications")) {
    return publications instanceof Error ? Promise.reject(publications) : Promise.resolve(publications);
  }
  return Promise.resolve(agentBody);
}

beforeEach(() => {
  apiMock.get.mockReset();
  seq = 0;
});

describe("PublicationHistoryPanel", () => {
  it("requests the real URL and renders receipt rows keyed by id", async () => {
    apiMock.get.mockResolvedValue(pubList([receipt(), receipt({ version: 2 })]));
    render(<PublicationHistoryPanel agentId={AGENT_ID} />);
    await screen.findByTestId("publications-pagination");
    expect(apiMock.get).toHaveBeenCalledWith(
      `/api/agents/${AGENT_ID}/publications?limit=20&offset=0`
    );
    expect(screen.getAllByTestId("publication-row")).toHaveLength(2);
  });

  it("shows the four outcomes with distinct wording, never a shared pass label", async () => {
    apiMock.get.mockResolvedValue(pubList([
      receipt({ outcome: "passed" }),
      receipt({ outcome: "warn_passed" }),
      receipt({ outcome: "force_bypassed" }),
      receipt({ outcome: "gate_off" }),
    ]));
    render(<PublicationHistoryPanel agentId={AGENT_ID} />);
    expect(await screen.findByText("门禁通过")).toBeInTheDocument();
    expect(screen.getByText("告警模式放行")).toBeInTheDocument();
    expect(screen.getByText("强制放行（绕过门禁）")).toBeInTheDocument();
    expect(screen.getByText("门禁未启用")).toBeInTheDocument();
    expect(screen.queryByText("评测通过")).not.toBeInTheDocument();
    expect(screen.queryByText(/当前生效/)).not.toBeInTheDocument();
  });

  it("falls back to the raw string for an unknown outcome", async () => {
    apiMock.get.mockResolvedValue(pubList([receipt({ outcome: "mystery" })]));
    render(<PublicationHistoryPanel agentId={AGENT_ID} />);
    expect(await screen.findByText("mystery")).toBeInTheDocument();
    expect(screen.queryByText("门禁通过")).not.toBeInTheDocument();
  });

  it("keeps duplicate publications of one version as separate rows", async () => {
    apiMock.get.mockResolvedValue(pubList([
      receipt({ version: 3, created_at: "2026-10-18T09:00:00Z" }),
      receipt({ version: 3, created_at: "2026-10-18T11:00:00Z", outcome: "force_bypassed" }),
    ]));
    render(<PublicationHistoryPanel agentId={AGENT_ID} />);
    await screen.findByTestId("publications-pagination");
    const rows = screen.getAllByTestId("publication-row");
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).not.toContain("强制放行");
    expect(rows[1].textContent).toContain("强制放行（绕过门禁）");
  });

  it("expands frozen receipt evidence without querying latest evaluations", async () => {
    apiMock.get.mockResolvedValue(pubList([receipt({
      baseline_run_id: "run-base-1",
      baseline_run_hash: "base-hash-1",
    })]));
    const user = userEvent.setup();
    render(<PublicationHistoryPanel agentId={AGENT_ID} />);
    await user.click(await screen.findByRole("button", { name: "展开证据" }));
    const evidence = await screen.findByTestId(/evidence-receipt-/);
    expect(evidence.textContent).toContain("hash-1");
    expect(evidence.textContent).toContain("run-cand-1");
    expect(evidence.textContent).toContain("cand-hash-1");
    expect(evidence.textContent).toContain("run-base-1");
    expect(evidence.textContent).toContain("该次发布时点冻结的凭据快照");
    // Structured report rendering from the snapshot only.
    expect(screen.getByTestId("gate-report-structured")).toBeInTheDocument();
    expect(screen.getByTestId("gate-signal").textContent).toContain("deterministic_pass_rate：通过");
    // Only the history query fired; no evaluation queries were made.
    expect(apiMock.get).toHaveBeenCalledTimes(1);
  });

  it("renders missing baseline as 未记录, not fabricated data", async () => {
    apiMock.get.mockResolvedValue(pubList([receipt()]));
    const user = userEvent.setup();
    render(<PublicationHistoryPanel agentId={AGENT_ID} />);
    await user.click(await screen.findByRole("button", { name: "展开证据" }));
    const evidence = await screen.findByTestId(/evidence-receipt-/);
    expect(evidence.textContent).toContain("baseline 评测 Run");
    expect(evidence.textContent).toContain("未记录");
  });

  it("falls back to raw JSON for a malformed gate report snapshot", async () => {
    apiMock.get.mockResolvedValue(pubList([receipt({
      gate_report: { unexpected_field: { nested: true } },
    })]));
    const user = userEvent.setup();
    render(<PublicationHistoryPanel agentId={AGENT_ID} />);
    await user.click(await screen.findByRole("button", { name: "展开证据" }));
    const raw = await screen.findByTestId("gate-report-raw");
    expect(raw.textContent).toContain("unexpected_field");
    expect(screen.queryByTestId("gate-report-structured")).not.toBeInTheDocument();
  });

  it("renders gate_off report snapshot without pass semantics", async () => {
    apiMock.get.mockResolvedValue(pubList([receipt({
      outcome: "gate_off",
      gate_config: null,
      gate_report: { mode: "off" },
      candidate_run_id: null,
      candidate_run_hash: null,
    })]));
    const user = userEvent.setup();
    render(<PublicationHistoryPanel agentId={AGENT_ID} />);
    expect(await screen.findByText("门禁未启用")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "展开证据" }));
    const evidence = await screen.findByTestId(/evidence-receipt-/);
    expect(evidence.textContent).toContain("mode");
    expect(evidence.textContent).not.toContain("通过率");
  });

  it("shows empty history as no receipts with the early-publication note", async () => {
    apiMock.get.mockResolvedValue(pubList([], 0));
    render(<PublicationHistoryPanel agentId={AGENT_ID} />);
    const empty = await screen.findByTestId("publications-empty");
    expect(empty.textContent).toContain("暂无发布凭据");
    expect(empty.textContent).toContain("可能没有记录");
  });

  it("keeps pagination on an empty later page and returns to the previous one", async () => {
    apiMock.get
      .mockResolvedValueOnce(pubList([receipt()], 21))
      .mockResolvedValueOnce(pubList([], 21))
      .mockResolvedValueOnce(pubList([receipt()], 21));
    const user = userEvent.setup();
    render(<PublicationHistoryPanel agentId={AGENT_ID} />);
    await screen.findByTestId("publication-row");
    await user.click(screen.getByRole("button", { name: "下一页" }));
    expect(apiMock.get).toHaveBeenLastCalledWith(
      `/api/agents/${AGENT_ID}/publications?limit=20&offset=20`
    );
    const emptyPage = await screen.findByTestId("publications-empty-page");
    expect(emptyPage.textContent).toContain("当前页暂无发布凭据");
    const prev = screen.getByRole("button", { name: "上一页" });
    expect(prev).toBeEnabled();
    await user.click(prev);
    expect(apiMock.get).toHaveBeenLastCalledWith(
      `/api/agents/${AGENT_ID}/publications?limit=20&offset=0`
    );
    await screen.findByTestId("publication-row");
  });

  it("shows error with detail.error code/message and recovers on retry", async () => {
    const failure = Object.assign(new Error("Request failed"), {
      detail: { error: { code: "NOT_FOUND", message: "Agent agent-1 not found" } },
    });
    apiMock.get
      .mockRejectedValueOnce(failure)
      .mockResolvedValueOnce(pubList([receipt()]));
    const user = userEvent.setup();
    render(<PublicationHistoryPanel agentId={AGENT_ID} />);
    await screen.findByTestId("publications-error");
    expect(screen.getByRole("alert").textContent).toContain("[NOT_FOUND]");
    expect(screen.getByRole("alert").textContent).toContain("Agent agent-1 not found");
    await user.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByTestId("publication-row")).toBeInTheDocument();
  });

  it("allows refresh in every state", async () => {
    const pending = deferred<PublicationListResponse>();
    apiMock.get.mockReturnValue(pending.promise);
    const user = userEvent.setup();
    render(<PublicationHistoryPanel agentId={AGENT_ID} />);
    expect(await screen.findByTestId("publications-loading")).toBeInTheDocument();
    // loading state: refresh is rendered and enabled
    const refresh = screen.getByTestId("publications-refresh");
    expect(refresh).toBeEnabled();
    await user.click(refresh);
    expect(apiMock.get).toHaveBeenCalledTimes(2);
    pending.resolve(pubList([], 0));
    // empty state
    await screen.findByTestId("publications-empty");
    await user.click(screen.getByTestId("publications-refresh"));
    expect(apiMock.get).toHaveBeenCalledTimes(3);
  });

  it("renders only the latest request when refresh supersedes an in-flight page", async () => {
    const page1 = deferred<PublicationListResponse>();
    const page2 = deferred<PublicationListResponse>();
    const page2Refresh = deferred<PublicationListResponse>();
    apiMock.get
      .mockReturnValueOnce(page1.promise)
      .mockReturnValueOnce(page2.promise)
      .mockReturnValueOnce(page2Refresh.promise);
    const user = userEvent.setup();
    render(<PublicationHistoryPanel agentId={AGENT_ID} />);
    page1.resolve(pubList([receipt({ created_at: "PAGE-1" })], 41));
    await screen.findByTestId("publication-row");
    await user.click(screen.getByRole("button", { name: "下一页" }));
    // Refresh issues a second offset=20 request while the first one is in flight.
    await user.click(screen.getByTestId("publications-refresh"));
    // The superseded offset=20 response arrives first and must be discarded.
    page2.resolve(pubList([receipt({ created_at: "SUPERSEDED-20" })], 41));
    await waitFor(() => {
      expect(screen.queryByText(/SUPERSEDED-20/)).not.toBeInTheDocument();
    });
    page2Refresh.resolve(pubList([receipt({ created_at: "LATEST-20" })], 41));
    await screen.findByTestId("publication-row");
    expect(screen.getByText(/LATEST-20/)).toBeInTheDocument();
    expect(screen.queryByText(/SUPERSEDED-20/)).not.toBeInTheDocument();
  });

  it("ignores a stale same-parameter response superseded by refresh", async () => {
    const stale = deferred<PublicationListResponse>();
    const fresh = deferred<PublicationListResponse>();
    apiMock.get
      .mockReturnValueOnce(stale.promise)
      .mockReturnValueOnce(fresh.promise);
    const user = userEvent.setup();
    render(<PublicationHistoryPanel agentId={AGENT_ID} />);
    await screen.findByTestId("publications-loading");
    await user.click(screen.getByTestId("publications-refresh"));
    stale.resolve(pubList([receipt({ created_at: "STALE-PAGE" })]));
    await waitFor(() => {
      expect(screen.queryByText(/STALE-PAGE/)).not.toBeInTheDocument();
    });
    expect(screen.getByTestId("publications-loading")).toBeInTheDocument();
    fresh.resolve(pubList([receipt({ created_at: "FRESH-PAGE" })]));
    await screen.findByTestId("publication-row");
    expect(screen.getByText(/FRESH-PAGE/)).toBeInTheDocument();
    expect(screen.queryByText(/STALE-PAGE/)).not.toBeInTheDocument();
  });

  it("discards responses from a previous agent after the agent prop changes", async () => {
    const staleA = deferred<PublicationListResponse>();
    const freshB = deferred<PublicationListResponse>();
    apiMock.get
      .mockReturnValueOnce(staleA.promise)
      .mockReturnValueOnce(freshB.promise);
    const { rerender } = render(<PublicationHistoryPanel agentId="agent-a" />);
    await screen.findByTestId("publications-loading");
    rerender(<PublicationHistoryPanel agentId="agent-b" />);
    staleA.resolve(pubList([receipt({ created_at: "AGENT-A-DATA" })]));
    await waitFor(() => {
      expect(screen.queryByText(/AGENT-A-DATA/)).not.toBeInTheDocument();
    });
    freshB.resolve(pubList([receipt({ created_at: "AGENT-B-DATA" })]));
    await screen.findByTestId("publication-row");
    expect(screen.getByText(/AGENT-B-DATA/)).toBeInTheDocument();
  });

  it("drops a late error from a superseded request", async () => {
    const stale = deferred<PublicationListResponse>();
    const fresh = deferred<PublicationListResponse>();
    apiMock.get
      .mockReturnValueOnce(stale.promise)
      .mockReturnValueOnce(fresh.promise);
    const user = userEvent.setup();
    render(<PublicationHistoryPanel agentId={AGENT_ID} />);
    await screen.findByTestId("publications-loading");
    await user.click(screen.getByTestId("publications-refresh"));
    stale.promise.catch(() => {});
    stale.reject(new Error("stale failure"));
    fresh.resolve(pubList([receipt()]));
    await screen.findByTestId("publication-row");
    expect(screen.queryByTestId("publications-error")).not.toBeInTheDocument();
  });
});

describe("Agent detail page assembly", () => {
  it("mounts the publication history panel below the version panel", async () => {
    apiMock.get.mockImplementation((url: string) =>
      byUrl(url, pubList([receipt()]), {
        id: AGENT_ID, name: "Bot", mode: "chat", tools: [], skills: [],
        knowledge_base_ids: [], risk_level: "low", enable_suggestions: false,
        model_config: { model: "gpt-x" },
      })
    );
    render(<AgentDetailPage />);
    expect(await screen.findByText("Bot")).toBeInTheDocument();
    expect(screen.getByText("versions")).toBeInTheDocument();
    const panel = await screen.findByTestId("publication-history-panel");
    expect(panel).toBeInTheDocument();
    // The panel sits between the version panel and the channel manager.
    const versionEl = screen.getByText("versions");
    const channelEl = screen.getByText("channels");
    expect(versionEl.compareDocumentPosition(channelEl) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(
      versionEl.compareDocumentPosition(panel) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy();
    expect(
      panel.compareDocumentPosition(channelEl) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy();
  });
});

describe("publication history review regressions", () => {
  it.each(["constructor", "__proto__", "toString"])("preserves prototype-like unknown outcome %s", (outcome) => {
    expect(outcomeLabel(outcome)).toBe(outcome);
    expect(outcomeTone(outcome)).toBe("default");
  });

  it("resets pagination when switching to another agent", async () => {
    apiMock.get
      .mockResolvedValueOnce(pubList([receipt()], 21))
      .mockResolvedValueOnce(pubList([receipt()], 21))
      .mockResolvedValueOnce(pubList([receipt({ agent_id: "agent-b" })], 1));
    const { rerender } = render(<PublicationHistoryPanel agentId="agent-a" />);
    await screen.findByTestId("publication-row");
    await userEvent.click(screen.getByRole("button", { name: "下一页" }));
    await screen.findByTestId("publication-row");
    rerender(<PublicationHistoryPanel agentId="agent-b" />);
    await screen.findByTestId("publication-row");
    expect(apiMock.get).toHaveBeenLastCalledWith("/api/agents/agent-b/publications?limit=20&offset=0");
    expect(screen.getByRole("button", { name: "上一页" })).toBeDisabled();
  });

  it("does not interpret missing bypass evidence as false", async () => {
    apiMock.get.mockResolvedValue(pubList([receipt({ gate_report: { mode: "off" } })]));
    render(<PublicationHistoryPanel agentId={AGENT_ID} />);
    await userEvent.click(await screen.findByRole("button", { name: "展开证据" }));
    const field = screen.getByText("force 绕过：").parentElement;
    expect(field).toHaveTextContent("未记录");
    expect(field).not.toHaveTextContent("否");
  });

  it("keeps the gate configuration field labeled when the snapshot is missing", async () => {
    apiMock.get.mockResolvedValue(pubList([receipt({ gate_config: null })]));
    render(<PublicationHistoryPanel agentId={AGENT_ID} />);
    await userEvent.click(await screen.findByRole("button", { name: "展开证据" }));
    expect(screen.getByText("门禁配置（发布时冻结）：").parentElement).toHaveTextContent("未记录");
  });

  it("displays the recorded Run hashes independently of nullable Run IDs", async () => {
    apiMock.get.mockResolvedValue(pubList([receipt({
      candidate_run_id: null, candidate_run_hash: "recorded-candidate-hash",
      baseline_run_id: null, baseline_run_hash: "recorded-baseline-hash",
    })]));
    render(<PublicationHistoryPanel agentId={AGENT_ID} />);
    await userEvent.click(await screen.findByRole("button", { name: "展开证据" }));
    expect(screen.getByText("recorded-candidate-hash")).toBeInTheDocument();
    expect(screen.getByText("recorded-baseline-hash")).toBeInTheDocument();
  });

  it("keeps the previous page reachable when a later page query fails", async () => {
    apiMock.get
      .mockResolvedValueOnce(pubList([receipt()], 21))
      .mockRejectedValueOnce({ detail: { error: { code: "UNAVAILABLE", message: "Try again" } } })
      .mockResolvedValueOnce(pubList([receipt()], 21));
    render(<PublicationHistoryPanel agentId={AGENT_ID} />);
    await screen.findByTestId("publication-row");
    await userEvent.click(screen.getByRole("button", { name: "下一页" }));
    await screen.findByTestId("publications-error");
    await userEvent.click(screen.getByRole("button", { name: "上一页" }));
    await screen.findByTestId("publication-row");
    expect(apiMock.get).toHaveBeenLastCalledWith(`/api/agents/${AGENT_ID}/publications?limit=20&offset=0`);
  });

  it("discards the first A request after switching A to B and back to A", async () => {
    const stale = deferred<PublicationListResponse>();
    apiMock.get
      .mockReturnValueOnce(stale.promise)
      .mockResolvedValueOnce(pubList([receipt({ created_at: "B-RESULT" })]))
      .mockResolvedValueOnce(pubList([receipt({ created_at: "CURRENT-A" })]));
    const { rerender } = render(<PublicationHistoryPanel agentId="agent-a" />);
    rerender(<PublicationHistoryPanel agentId="agent-b" />);
    await screen.findByText("B-RESULT");
    rerender(<PublicationHistoryPanel agentId="agent-a" />);
    await screen.findByText("CURRENT-A");
    await act(async () => {
      stale.resolve(pubList([receipt({ created_at: "OLD-A" })]));
      await stale.promise;
    });
    expect(screen.queryByText("OLD-A")).not.toBeInTheDocument();
    expect(screen.getByText("CURRENT-A")).toBeInTheDocument();
  });
});
