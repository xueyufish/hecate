// RunEventsPanel live observation: every case drives the REAL adapter —
// global fetch stubbed at the transport level, frames flowing through
// ApiClient's reader/decoder, the SSE frame parser, the envelope mapping
// and the panel's observation state machine. Nothing mocks a final state.

import { act, fireEvent, render, screen, within } from "@testing-library/react";
import type { RenderResult } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RunEventsPanel } from "@/components/tasks/run-events-panel";
import { asRunEventItem, RUN_TERMINAL_SCHEMA } from "@/lib/run-event-types";

const encoder = new TextEncoder();

function envelope(
  id: string,
  seq: number,
  opts: { kind?: "event" | "terminal" | "gap"; schema?: string } = {},
): string {
  const body: Record<string, unknown> = {
    event_id: id,
    kind: opts.kind ?? "event",
    source_sequence: seq,
    occurred_at: "2026-10-27T00:00:00+00:00",
    received_at: "2026-10-27T00:00:01+00:00",
  };
  if ((opts.kind ?? "event") === "gap") body.gap = { from_sequence: seq - 1, to_sequence: seq };
  else body.payload_schema_ref = opts.schema ?? `schema/${id}`;
  return JSON.stringify(body);
}

function frame(data: string, event?: string): string {
  return event ? `event: ${event}\ndata: ${data}\n\n` : `data: ${data}\n\n`;
}

function sseResponse(chunks: string[]): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return { ok: true, status: 200, body } as Response;
}

function openSse(): { response: Response; push: (chunk: string) => void; cancelled: ReturnType<typeof vi.fn> } {
  let streamController: ReadableStreamDefaultController<Uint8Array> | null = null;
  const cancelled = vi.fn();
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      streamController = c;
    },
    cancel() {
      cancelled();
      streamController = null;
    },
  });
  return {
    response: { ok: true, status: 200, body } as Response,
    cancelled,
    push(chunk: string) {
      streamController?.enqueue(encoder.encode(chunk));
    },
  };
}

function jsonPage(events: string[], nextCursor: string | null, hasMore: boolean): Response {
  return {
    ok: true,
    status: 200,
    json: async () => ({
      events: events.map((raw) => JSON.parse(raw)),
      next_cursor: nextCursor,
      has_more: hasMore,
    }),
  } as Response;
}

function errorResponse(status: number, code: string): Response {
  return {
    ok: false,
    status,
    statusText: code,
    json: async () => ({ error: { code, message: `${code} for stream`, details: null } }),
  } as Response;
}

const PAGE_URL = "/api/runs/run-1/events";
const STREAM_URL = "/api/runs/run-1/events/stream";

let fetchMock: ReturnType<typeof vi.fn>;

function streamCalls(): Array<{ url: string; signal: AbortSignal }> {
  return fetchMock.mock.calls
    .filter(([url]) => String(url).includes("/events/stream"))
    .map(([url, init]) => ({ url: String(url), signal: (init as RequestInit).signal as AbortSignal }));
}

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

async function renderPanelWithFirstPage(onTerminal?: () => void): Promise<RenderResult> {
  fetchMock.mockImplementation((url: unknown) => {
    const path = String(url);
    if (path === PAGE_URL) {
      return Promise.resolve(jsonPage([envelope("e-1", 1), envelope("e-2", 2)], "2", true));
    }
    if (path.startsWith(STREAM_URL)) return Promise.resolve(sseResponse([]));
    return Promise.reject(new Error(`unexpected path: ${path}`));
  });
  const view = render(<RunEventsPanel runId="run-1" onTerminal={onTerminal} />);
  await screen.findByText("e-1");
  return view;
}

describe("RunEventsPanel live observation", () => {
  it("stops on the actual FastAPI rejection envelope without retrying", async () => {
    await renderPanelWithFirstPage();
    fetchMock.mockImplementation((url: unknown) => String(url).includes("/events/stream")
      ? Promise.resolve(new Response(JSON.stringify({ detail: { error: { code: "FORBIDDEN", message: "viewer required" } } }), { status: 403 }))
      : Promise.resolve(jsonPage([], null, false)));
    vi.useFakeTimers();
    await act(async () => { fireEvent.click(screen.getByTestId("live-start")); });
    expect(screen.getByTestId("live-error")).toHaveTextContent("FORBIDDEN");
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(streamCalls()).toHaveLength(1);
  });

  it("reports an idle accepted connection as connected", async () => {
    await renderPanelWithFirstPage();
    const open = openSse();
    fetchMock.mockImplementation(() => Promise.resolve(open.response));
    await act(async () => { fireEvent.click(screen.getByTestId("live-start")); });
    expect(screen.getByTestId("live-status")).toHaveTextContent("已连接");
  });

  it("never moves the resume cursor backwards on an older replay", async () => {
    await renderPanelWithFirstPage();
    const streams = [
      sseResponse([frame(envelope("e-4", 4)), frame(envelope("e-3", 3))]),
      sseResponse([frame(envelope("t-5", 5, { kind: "terminal" }))]),
    ];
    fetchMock.mockImplementation((url: unknown) => String(url).includes("/events/stream")
      ? Promise.resolve(streams.shift())
      : Promise.resolve(new Response(JSON.stringify({ projection: { state: "running" } }))));
    vi.useFakeTimers();
    await act(async () => { fireEvent.click(screen.getByTestId("live-start")); });
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(streamCalls()[1]?.url).toBe(`${STREAM_URL}?cursor=4`);
    expect(screen.queryByText("e-3")).not.toBeInTheDocument();
  });

  it("does not replenish the retry budget when a connection only replays old data", async () => {
    await renderPanelWithFirstPage();
    fetchMock.mockImplementation((url: unknown) => String(url).includes("/events/stream")
      ? Promise.resolve(sseResponse([frame(envelope("e-3", 3))]))
      : Promise.resolve(new Response(JSON.stringify({ projection: { state: "running" } }))));
    vi.useFakeTimers();
    await act(async () => { fireEvent.click(screen.getByTestId("live-start")); });
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(screen.getByTestId("live-status")).toHaveTextContent("观察已停止");
    expect(streamCalls()).toHaveLength(7);
  });

  it("does not reopen an abandoned subscription after Run A -> B -> A", async () => {
    const view = await renderPanelWithFirstPage();
    const oldStream = openSse();
    fetchMock.mockImplementation((url: unknown) => String(url).includes("/events/stream")
      ? Promise.resolve(oldStream.response)
      : Promise.resolve(jsonPage([], null, false)));
    await act(async () => { fireEvent.click(screen.getByTestId("live-start")); });
    view.rerender(<RunEventsPanel runId="run-2" />);
    await screen.findByTestId("run-events-empty");
    view.rerender(<RunEventsPanel runId="run-1" />);
    await screen.findByTestId("run-events-empty");
    expect(screen.getByTestId("live-status")).toHaveTextContent("未开启");
    expect(streamCalls()).toHaveLength(1);
  });

  it("confirms terminal EOF with the Run query and stops reconnecting", async () => {
    const onTerminal = vi.fn();
    await renderPanelWithFirstPage(onTerminal);
    fetchMock.mockImplementation((url: unknown) => {
      if (String(url).includes("/events/stream")) return Promise.resolve(sseResponse([]));
      if (String(url) === "/api/runs/run-1") return Promise.resolve(new Response(JSON.stringify({ projection: { state: "failed" } })));
      return Promise.resolve(jsonPage([], null, false));
    });
    vi.useFakeTimers();
    await act(async () => { fireEvent.click(screen.getByTestId("live-start")); });
    expect(fetchMock).toHaveBeenCalledWith("/api/runs/run-1", expect.anything());
    expect(screen.getByTestId("live-status")).toHaveTextContent("观察结束");
    expect(screen.getByTestId("live-status")).not.toHaveTextContent("已收到终态事件");
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(streamCalls()).toHaveLength(1);
    expect(onTerminal).toHaveBeenCalledOnce();
  });

  it("starts after the page tail, appends live events and resumes by source_sequence", async () => {
    await renderPanelWithFirstPage();
    const open = openSse();
    fetchMock.mockImplementation((url: unknown) => {
      const path = String(url);
      if (path === PAGE_URL) {
        return Promise.resolve(jsonPage([envelope("e-1", 1), envelope("e-2", 2)], "2", true));
      }
      if (path.startsWith(STREAM_URL)) return Promise.resolve(open.response);
      return Promise.reject(new Error(path));
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("live-start"));
      open.push(frame(envelope("e-3", 3)));
      open.push(frame(envelope("e-4", 4)));
    });
    expect(await screen.findByText("e-3")).toBeInTheDocument();
    expect(screen.getByText("e-4")).toBeInTheDocument();
    expect(screen.getByTestId("live-status").textContent).toContain("已连接");
    // The subscription anchored at the page's own resume position.
    expect(streamCalls()[0]?.url).toBe(`${STREAM_URL}?cursor=2`);
  });

  it("ends observation on the platform terminal schema without reconnecting", async () => {
    await renderPanelWithFirstPage();
    fetchMock.mockImplementation((url: unknown) =>
      String(url).startsWith(STREAM_URL)
        ? Promise.resolve(
            sseResponse([
              frame(envelope("e-3", 3)),
              frame(envelope("t-4", 4, { schema: RUN_TERMINAL_SCHEMA })),
            ]),
          )
        : Promise.resolve(jsonPage([envelope("e-1", 1)], "1", false)),
    );

    await act(async () => {
      fireEvent.click(screen.getByTestId("live-start"));
    });
    expect(await screen.findByText("t-4")).toBeInTheDocument();
    const status = screen.getByTestId("live-status").textContent ?? "";
    expect(status).toContain("观察结束");
    expect(status).toContain("以任务详情为准");
    expect(streamCalls()).toHaveLength(1);
  });

  it("ends with the server's explicit error frame and never retries or refreshes", async () => {
    await renderPanelWithFirstPage();
    fetchMock.mockImplementation((url: unknown) =>
      String(url).startsWith(STREAM_URL)
        ? Promise.resolve(sseResponse([frame(JSON.stringify({ code: 403 }), "error")]))
        : Promise.resolve(jsonPage([envelope("e-1", 1)], "1", false)),
    );

    await act(async () => {
      fireEvent.click(screen.getByTestId("live-start"));
    });
    expect(await screen.findByTestId("live-error")).toHaveTextContent("[403]");
    expect(screen.getByTestId("live-status").textContent).toContain("观察已终止");
    expect(screen.getByTestId("live-start")).toBeInTheDocument();
    expect(streamCalls()).toHaveLength(1);
  });

  it("treats a pre-connection HTTP rejection as terminal", async () => {
    await renderPanelWithFirstPage();
    fetchMock.mockImplementation((url: unknown) =>
      String(url).startsWith(STREAM_URL)
        ? Promise.resolve(errorResponse(404, "NOT_FOUND"))
        : Promise.resolve(jsonPage([envelope("e-1", 1)], "1", false)),
    );

    await act(async () => {
      fireEvent.click(screen.getByTestId("live-start"));
    });
    const liveError = await screen.findByTestId("live-error");
    expect(liveError).toHaveTextContent("NOT_FOUND");
    expect(streamCalls()).toHaveLength(1);
  });

  it("reconnects from the last accepted envelope after a dropped connection", async () => {
    await renderPanelWithFirstPage();
    const attempts = [
      sseResponse([frame(envelope("e-3", 3))]), // drops after e-3
      sseResponse([frame(envelope("t-4", 4, { schema: RUN_TERMINAL_SCHEMA }))]),
    ];
    fetchMock.mockImplementation((url: unknown) => {
      const path = String(url);
      if (path === PAGE_URL) return Promise.resolve(jsonPage([envelope("e-1", 1)], "1", false));
      if (path.startsWith(STREAM_URL)) return Promise.resolve(attempts.shift());
      return Promise.reject(new Error(path));
    });

    vi.useFakeTimers();
    await act(async () => {
      fireEvent.click(screen.getByTestId("live-start"));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByText("e-3")).toBeInTheDocument();
    expect(screen.getByTestId("live-status").textContent).toContain("正在重连");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    const calls = streamCalls();
    expect(calls).toHaveLength(2);
    expect(calls[1]?.url).toBe(`${STREAM_URL}?cursor=3`);
    expect(screen.getByTestId("live-status").textContent).toContain("观察结束");
  });

  it("stops reconnecting after the bounded budget and points to the authoritative state", async () => {
    await renderPanelWithFirstPage();
    fetchMock.mockImplementation((url: unknown) => {
      const path = String(url);
      if (path === PAGE_URL) return Promise.resolve(jsonPage([envelope("e-1", 1)], "1", false));
      // Every attempt closes cleanly without any frame or terminal event.
      if (path.startsWith(STREAM_URL)) return Promise.resolve(sseResponse([]));
      return Promise.reject(new Error(path));
    });

    vi.useFakeTimers();
    await act(async () => {
      fireEvent.click(screen.getByTestId("live-start"));
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      // 6 retries with capped backoff: 0.5+1+2+4+8+8s fits in 24s.
      await vi.advanceTimersByTimeAsync(24_000);
    });
    expect(streamCalls()).toHaveLength(7); // initial + 6 retries
    expect(screen.getByTestId("live-status").textContent).toContain("观察已停止");
    expect(screen.getByTestId("live-requery")).toBeInTheDocument();
  });

  it("stops on malformed data without advancing the cursor or reconnecting", async () => {
    await renderPanelWithFirstPage();
    fetchMock.mockImplementation((url: unknown) => {
      const path = String(url);
      if (path === PAGE_URL) return Promise.resolve(jsonPage([envelope("e-1", 1)], "1", false));
      if (path.startsWith(STREAM_URL)) {
        return Promise.resolve(
          sseResponse([frame(envelope("e-3", 3)), frame("this is not json")]),
        );
      }
      return Promise.reject(new Error(path));
    });

    vi.useFakeTimers();
    await act(async () => {
      fireEvent.click(screen.getByTestId("live-start"));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByTestId("live-error")).toHaveTextContent("无法解析");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(streamCalls()).toHaveLength(1); // no reconnect: data error is terminal
  });

  it("dedupes repeated deliveries and renders server-provided gap rows", async () => {
    await renderPanelWithFirstPage();
    fetchMock.mockImplementation((url: unknown) => {
      const path = String(url);
      if (path === PAGE_URL) return Promise.resolve(jsonPage([envelope("e-1", 1)], "1", false));
      if (path.startsWith(STREAM_URL)) {
        return Promise.resolve(
          sseResponse([
            frame(envelope("e-3", 3)),
            frame(envelope("e-3", 3)), // replayed delivery, same event id
            frame(envelope("gap-4", 4, { kind: "gap" })),
          ]),
        );
      }
      return Promise.reject(new Error(path));
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("live-start"));
    });
    expect(await screen.findByText("e-3")).toBeInTheDocument();
    const live = screen.getByTestId("live-events");
    expect(within(live).getAllByText("e-3")).toHaveLength(1);
    expect(within(live).getByTestId("run-events-gap")).toHaveTextContent("缺失事件");
  });

  it("aborts the stream when the user stops observing", async () => {
    await renderPanelWithFirstPage();
    const open = openSse();
    fetchMock.mockImplementation((url: unknown) =>
      String(url).startsWith(STREAM_URL)
        ? Promise.resolve(open.response)
        : Promise.resolve(jsonPage([envelope("e-1", 1)], "1", false)),
    );

    await act(async () => {
      fireEvent.click(screen.getByTestId("live-start"));
    });
    expect(await screen.findByTestId("live-stop")).toBeInTheDocument();
    const signal = streamCalls()[0]?.signal;

    await act(async () => {
      fireEvent.click(screen.getByTestId("live-stop"));
    });
    expect(screen.getByTestId("live-status").textContent).toContain("未开启");
    expect(signal?.aborted).toBe(true);
    expect(open.cancelled).toHaveBeenCalledOnce();
    // A late frame from the stopped stream never renders.
    open.push(frame(envelope("e-9", 9)));
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.queryByText("e-9")).not.toBeInTheDocument();
  });

  it("aborts the old stream on Run switch and ignores its late frames", async () => {
    const view = await renderPanelWithFirstPage();
    const oldStream = openSse();
    fetchMock.mockImplementation((url: unknown) => {
      const path = String(url);
      if (path === "/api/runs/run-2/events") {
        return Promise.resolve(jsonPage([envelope("r2-1", 1)], "1", false));
      }
      if (path.startsWith(STREAM_URL)) return Promise.resolve(oldStream.response);
      return Promise.reject(new Error(path));
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("live-start"));
    });
    expect(await screen.findByTestId("live-stop")).toBeInTheDocument();
    const oldSignal = streamCalls()[0]?.signal;

    view.rerender(<RunEventsPanel runId="run-2" />);
    await screen.findByText("r2-1");
    expect(oldSignal?.aborted).toBe(true);
    expect(oldStream.cancelled).toHaveBeenCalledOnce();
    expect(screen.getByTestId("live-status").textContent).toContain("未开启");

    oldStream.push(frame(envelope("e-8", 8)));
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.queryByText("e-8")).not.toBeInTheDocument();
  });

  it("reassembles frames split across chunk boundaries, UTF-8 code points and CRLF", async () => {
    await renderPanelWithFirstPage();
    const text = frame(envelope("事件-8", 8));
    const bytes = encoder.encode(text);
    const splitAt = bytes.indexOf(encoder.encode('{"c')[0]) + 1; // inside the JSON body
    fetchMock.mockImplementation((url: unknown) =>
      String(url).startsWith(STREAM_URL)
        ? Promise.resolve(
            sseResponse([
              new TextDecoder().decode(bytes.slice(0, splitAt)),
              new TextDecoder().decode(bytes.slice(splitAt)),
              frame(envelope("e-9", 9)).replace(/\n/g, "\r\n"),
            ]),
          )
        : Promise.resolve(jsonPage([envelope("e-1", 1)], "1", false)),
    );

    await act(async () => {
      fireEvent.click(screen.getByTestId("live-start"));
    });
    expect(await screen.findByText("事件-8")).toBeInTheDocument();
    expect(screen.getByText("e-9")).toBeInTheDocument();
  });

  it("bounds the live buffer and points older events to the history query", async () => {
    await renderPanelWithFirstPage();
    const bursts: string[] = [];
    for (let i = 0; i < 205; i += 1) bursts.push(frame(envelope(`b-${i}`, i + 10)));
    fetchMock.mockImplementation((url: unknown) =>
      String(url).startsWith(STREAM_URL)
        ? Promise.resolve(sseResponse(bursts))
        : Promise.resolve(jsonPage([envelope("e-1", 1)], "1", false)),
    );

    await act(async () => {
      fireEvent.click(screen.getByTestId("live-start"));
    });
    expect(await screen.findByText("b-204")).toBeInTheDocument();
    expect(screen.queryByText("b-0")).not.toBeInTheDocument();
    expect(screen.getByTestId("live-buffer-trimmed")).toBeInTheDocument();
    const rows = within(screen.getByTestId("live-events")).getAllByTestId("run-events-row");
    expect(rows).toHaveLength(200);
  });
});

describe("Run stream envelope validation", () => {
  it.each([
    { source_sequence: Number.MAX_SAFE_INTEGER + 1 },
    { kind: "gap", gap: { from_sequence: -1, to_sequence: 3 } },
    { kind: "gap", gap: { from_sequence: 4, to_sequence: 3 } },
  ])("rejects unsafe cursor or gap facts: %j", (overrides) => {
    expect(asRunEventItem({ ...JSON.parse(envelope("gap", 4)), ...overrides })).toBeNull();
  });

  it("keeps only the declared gap range", () => {
    const item = asRunEventItem({
      ...JSON.parse(envelope("gap", 4, { kind: "gap" })),
      gap: { from_sequence: 2, to_sequence: 3, context: "private context" },
    });
    expect(item?.gap).toEqual({ from_sequence: 2, to_sequence: 3 });
  });
});
