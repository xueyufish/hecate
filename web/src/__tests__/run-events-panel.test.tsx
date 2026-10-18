// RunEventsPanel behavior: cursor pagination, stale-response rejection,
// server-provided gap rendering, query-fact-only wording, error isolation.

import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RunEventsPanel } from "@/components/tasks/run-events-panel";
import type { RunEventItem, RunEventsPage } from "@/lib/run-event-types";

const apiMock = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("@/lib/api-client", () => ({ api: apiMock }));

function event(eventId: string, sequence: number, kind: "event" | "terminal" = "event"): RunEventItem {
  return {
    event_id: eventId, kind, source_sequence: sequence,
    occurred_at: "2026-10-18T00:00:00+00:00",
    received_at: "2026-10-18T00:00:01+00:00",
    payload_schema_ref: `schema/${eventId}`,
    source: `source-${eventId}`,
  };
}

function gapItem(fromSeq: number, toSeq: number): RunEventItem {
  return {
    event_id: `gap-${fromSeq}`, kind: "gap", source_sequence: toSeq,
    received_at: "2026-10-18T00:00:01+00:00",
    occurred_at: "2026-10-18T00:00:00+00:00", gap: { from_sequence: fromSeq, to_sequence: toSeq },
  };
}

function page(events: RunEventItem[], nextCursor: string | null, hasMore: boolean): RunEventsPage {
  return { events, next_cursor: nextCursor, has_more: hasMore };
}

function url(runId: string, cursor: string | null) {
  return cursor
    ? `/api/runs/${runId}/events?cursor=${encodeURIComponent(cursor)}`
    : `/api/runs/${runId}/events`;
}

beforeEach(() => {
  apiMock.get.mockReset();
});

describe("RunEventsPanel", () => {
  it("renders event fields from the first page", async () => {
    apiMock.get.mockResolvedValueOnce(page([event("e-1", 1), event("e-2", 2)], "c1", true));
    render(<RunEventsPanel runId="run-1" />);

    expect(await screen.findByTestId("run-events-table")).toBeInTheDocument();
    expect(screen.getByText("e-1")).toBeInTheDocument();
    expect(screen.getByText("schema/e-1")).toBeInTheDocument();
    expect(screen.getByText("source-e-1")).toBeInTheDocument();
    expect(apiMock.get).toHaveBeenCalledWith("/api/runs/run-1/events");
    expect(screen.getByRole("button", { name: "上一页" })).toBeDisabled();
  });

  it("pages forward with next_cursor and back with the popped cursor", async () => {
    apiMock.get
      .mockResolvedValueOnce(page([event("e-1", 1)], "c1", true))
      .mockResolvedValueOnce(page([event("e-2", 2)], null, false))
      .mockResolvedValueOnce(page([event("e-1", 1)], "c1", true));
    render(<RunEventsPanel runId="run-1" />);
    await screen.findByText("e-1");

    await userEvent.click(screen.getByRole("button", { name: "下一页" }));
    expect(await screen.findByText("e-2")).toBeInTheDocument();
    expect(apiMock.get).toHaveBeenLastCalledWith(url("run-1", "c1"));

    await userEvent.click(screen.getByRole("button", { name: "上一页" }));
    expect(await screen.findByText("e-1")).toBeInTheDocument();
    expect(apiMock.get).toHaveBeenLastCalledWith("/api/runs/run-1/events");
  });

  it("disables next and shows a query-only end note on the last page", async () => {
    apiMock.get.mockResolvedValueOnce(page([event("e-1", 1)], null, false));
    render(<RunEventsPanel runId="run-1" />);

    expect(await screen.findByTestId("run-events-end")).toHaveTextContent("已是最后一页");
    expect(screen.getByRole("button", { name: "下一页" })).toBeDisabled();
    expect(screen.queryByText(/已完成|成功/)).not.toBeInTheDocument();
  });

  it("shows an empty state without completion wording", async () => {
    apiMock.get.mockResolvedValueOnce(page([], null, false));
    render(<RunEventsPanel runId="run-1" />);

    expect(await screen.findByTestId("run-events-empty")).toHaveTextContent("暂无事件");
    expect(screen.queryByText(/已完成|成功/)).not.toBeInTheDocument();
  });

  it("refresh resets pagination back to the first page", async () => {
    apiMock.get
      .mockResolvedValueOnce(page([event("e-1", 1)], "c1", true))
      .mockResolvedValueOnce(page([event("e-2", 2)], null, false))
      .mockResolvedValueOnce(page([event("e-1", 1)], "c1", true));
    render(<RunEventsPanel runId="run-1" />);
    await screen.findByText("e-1");
    await userEvent.click(screen.getByRole("button", { name: "下一页" }));
    await screen.findByText("e-2");

    await userEvent.click(screen.getByRole("button", { name: "刷新" }));
    expect(await screen.findByText("e-1")).toBeInTheDocument();
    expect(apiMock.get).toHaveBeenLastCalledWith("/api/runs/run-1/events");
  });

  it("discards a stale first-page response after A -> B -> A switching", async () => {
    let releaseStale: (value: object) => void = () => {};
    const staleFirstPage = new Promise<object>((resolve) => { releaseStale = resolve; });
    let aCalls = 0;
    apiMock.get.mockImplementation((path: string) => {
      if (path === url("run-A", null)) {
        aCalls += 1;
        if (aCalls === 1) return staleFirstPage;
        return Promise.resolve(page([event("a-fresh", 9)], null, false));
      }
      if (path === url("run-B", null)) return Promise.resolve(page([event("b-1", 1)], null, false));
      return Promise.reject(new Error("unexpected"));
    });

    const view = render(<RunEventsPanel runId="run-A" />);
    view.rerender(<RunEventsPanel runId="run-B" />);
    expect(await screen.findByText("b-1")).toBeInTheDocument();
    view.rerender(<RunEventsPanel runId="run-A" />);
    expect(await screen.findByText("a-fresh")).toBeInTheDocument();

    await act(async () => {
      releaseStale(page([event("stale", 0)], null, false));
    });
    expect(screen.getByText("a-fresh")).toBeInTheDocument();
    expect(screen.queryByText("stale")).not.toBeInTheDocument();
    expect(screen.queryByText("b-1")).not.toBeInTheDocument();
  });

  it("drops a slow old-run error after switching to a healthy run", async () => {
    let rejectSlow: (reason: object) => void = () => {};
    const slowError = new Promise<object>((_, reject) => { rejectSlow = reject; });

    apiMock.get.mockImplementation((path: string) => {
      if (path === url("run-A", null)) return slowError;
      if (path === url("run-B", null)) return Promise.resolve(page([event("b-1", 1)], null, false));
      return Promise.reject(new Error("unexpected"));
    });

    const view = render(<RunEventsPanel runId="run-A" />);
    view.rerender(<RunEventsPanel runId="run-B" />);
    expect(await screen.findByText("b-1")).toBeInTheDocument();

    await act(async () => {
      rejectSlow({ detail: { error: { code: "TIMEOUT", message: "old run failed" } } });
    });
    expect(screen.getByText("b-1")).toBeInTheDocument();
    expect(screen.queryByTestId("run-events-error")).not.toBeInTheDocument();
  });

  it("renders a server-provided gap with its missing range", async () => {
    apiMock.get.mockResolvedValueOnce(page(
      [event("e-3", 3), gapItem(4, 7), event("e-8", 8)],
      null,
      false,
    ));
    render(<RunEventsPanel runId="run-1" />);

    const gap = await screen.findByTestId("run-events-gap");
    expect(gap).toHaveTextContent("缺失事件：序号 4–7");
    expect(screen.getByText("e-8")).toBeInTheDocument();
  });

  it("shows errors with retry on the same cursor, not a first-page reset", async () => {
    apiMock.get
      .mockResolvedValueOnce(page([event("e-1", 1)], "c1", true))
      .mockRejectedValueOnce({ detail: { error: { code: "TIMEOUT", message: "page failed" } } })
      .mockResolvedValueOnce(page([event("e-2", 2)], null, false));
    render(<RunEventsPanel runId="run-1" />);
    await screen.findByText("e-1");

    await userEvent.click(screen.getByRole("button", { name: "下一页" }));
    expect(await screen.findByTestId("run-events-error")).toHaveTextContent("TIMEOUT");

    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(apiMock.get).toHaveBeenLastCalledWith(url("run-1", "c1"));
    expect(await screen.findByText("e-2")).toBeInTheDocument();
  });
});


describe("RunEventsPanel pagination boundaries", () => {
  it("keeps previous-page navigation when a later page is empty", async () => {
    apiMock.get
      .mockResolvedValueOnce(page([event("first", 1)], "c1", true))
      .mockResolvedValueOnce(page([], "c1", false))
      .mockResolvedValueOnce(page([event("back", 1)], "c1", true));
    render(<RunEventsPanel runId="run-1" />);
    await screen.findByText("first");
    await userEvent.click(screen.getByRole("button", { name: "下一页" }));
    await screen.findByTestId("run-events-empty");
    expect(screen.getByTestId("run-events-end")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "上一页" })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: "上一页" }));
    expect(await screen.findByText("back")).toBeInTheDocument();
    expect(apiMock.get).toHaveBeenLastCalledWith(url("run-1", null));
  });

  it("uses has_more rather than an empty event list to allow the next page", async () => {
    apiMock.get
      .mockResolvedValueOnce(page([], "next", true))
      .mockResolvedValueOnce(page([event("after-empty", 2)], null, false));
    render(<RunEventsPanel runId="run-1" />);
    await screen.findByTestId("run-events-empty");
    expect(screen.queryByTestId("run-events-end")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "下一页" })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: "下一页" }));
    expect(await screen.findByText("after-empty")).toBeInTheDocument();
    expect(apiMock.get).toHaveBeenLastCalledWith(url("run-1", "next"));
  });

  it("disables next when the response has no usable next cursor", async () => {
    apiMock.get.mockResolvedValueOnce(page([event("first", 1)], null, true));
    render(<RunEventsPanel runId="run-1" />);
    await screen.findByText("first");
    expect(screen.getByRole("button", { name: "下一页" })).toBeDisabled();
    expect(screen.queryByTestId("run-events-end")).not.toBeInTheDocument();
  });

  it("preserves opaque cursor characters and server order across multiple pages", async () => {
    const cursor = "opaque +/=?&%中文";
    apiMock.get
      .mockResolvedValueOnce(page([event("later", 9), event("earlier", 1)], cursor, true))
      .mockResolvedValueOnce(page([event("second", 2)], "third", true))
      .mockResolvedValueOnce(page([event("third", 3)], null, false))
      .mockResolvedValueOnce(page([event("second-again", 2)], "third", true));
    render(<RunEventsPanel runId="run-1" />);
    await screen.findByText("later");
    expect(screen.getAllByTestId("run-events-row").map((row) => row.textContent))
      .toEqual([expect.stringContaining("later"), expect.stringContaining("earlier")]);
    await userEvent.click(screen.getByRole("button", { name: "下一页" }));
    await screen.findByText("second");
    const path = apiMock.get.mock.lastCall?.[0];
    expect(new URL(path, "https://example.test").searchParams.get("cursor")).toBe(cursor);
    await userEvent.click(screen.getByRole("button", { name: "下一页" }));
    await screen.findByText("third");
    await userEvent.click(screen.getByRole("button", { name: "上一页" }));
    await screen.findByText("second-again");
    expect(new URL(apiMock.get.mock.lastCall?.[0], "https://example.test").searchParams.get("cursor")).toBe(cursor);
  });

  it.each(["success", "error"])("rejects an old second-page %s after A -> B -> A", async (outcome) => {
    let resolveOld!: (value: object) => void;
    let rejectOld!: (reason: unknown) => void;
    const old = new Promise<object>((resolve, reject) => { resolveOld = resolve; rejectOld = reject; });
    apiMock.get
      .mockResolvedValueOnce(page([event("a-first", 1)], "a-next", true))
      .mockReturnValueOnce(old)
      .mockResolvedValueOnce(page([event("b-first", 2)], "b-next", true))
      .mockResolvedValueOnce(page([event("a-fresh", 3)], "fresh-next", true));
    const view = render(<RunEventsPanel runId="run-A" />);
    await screen.findByText("a-first");
    await userEvent.click(screen.getByRole("button", { name: "下一页" }));
    expect(screen.getByTestId("run-events-loading")).toBeInTheDocument();
    view.rerender(<RunEventsPanel runId="run-B" />);
    await screen.findByText("b-first");
    expect(screen.getByRole("button", { name: "上一页" })).toBeDisabled();
    view.rerender(<RunEventsPanel runId="run-A" />);
    await screen.findByText("a-fresh");
    await act(async () => {
      if (outcome === "success") resolveOld(page([event("stale-page", 4)], null, false));
      else rejectOld(new Error("stale-page-error"));
    });
    expect(screen.getByText("a-fresh")).toBeInTheDocument();
    expect(screen.queryByText("stale-page")).not.toBeInTheDocument();
    expect(screen.queryByTestId("run-events-error")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "上一页" })).toBeDisabled();
  });
});


it("renders terminal metadata without exposing payload or extra fields", async () => {
  const envelope = {
    ...event("terminal-id", 5, "terminal"), payload: { state: "payload-secret" }, extra: "extra-secret",
  };
  apiMock.get.mockResolvedValueOnce(page([envelope], null, false));
  render(<RunEventsPanel runId="run-1" />);
  expect(await screen.findByText("terminal-id")).toBeInTheDocument();
  expect(screen.getByText("terminal")).toBeInTheDocument();
  expect(screen.queryByText(/payload-secret|extra-secret/)).not.toBeInTheDocument();
});
