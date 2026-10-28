/** Regression tests for the auth-refresh handling in api-client. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiClient } from "@/lib/api-client";

function jsonResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response;
}

describe("ApiClient auth refresh", () => {
  beforeEach(() => {
    localStorage.setItem("access_token", "expired-token");
    localStorage.setItem("refresh_token", "valid-refresh-token");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("upload retries through token refresh after a 401", async () => {
    const client = new ApiClient();
    const fetchMock = vi.fn();
    // 1) upload -> 401, 2) refresh -> 200, 3) upload retry -> 201
    fetchMock.mockResolvedValueOnce(jsonResponse(401, { detail: "expired" }));
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, { access_token: "fresh-token", refresh_token: "rotated-refresh" })
    );
    fetchMock.mockResolvedValueOnce(jsonResponse(201, { id: "doc-1" }));
    vi.stubGlobal("fetch", fetchMock);

    const file = new File(["hello"], "notes.txt", { type: "text/plain" });
    const result = await client.upload("/api/knowledge-bases/kb/upload", file);

    expect(result).toEqual({ id: "doc-1" });
    expect(localStorage.getItem("access_token")).toBe("fresh-token");
    expect(localStorage.getItem("refresh_token")).toBe("rotated-refresh");
    // Retry must be the third call, carrying the fresh token.
    const retryInit = fetchMock.mock.calls[2][1] as RequestInit;
    expect((retryInit.headers as Record<string, string>).Authorization).toBe("Bearer fresh-token");
  });

  it("concurrent 401s share a single refresh call", async () => {
    const client = new ApiClient();
    const fetchMock = vi.fn();
    // Requests with the stale token 401; the refresh endpoint always
    // succeeds and hands out the same fresh token; requests carrying it
    // succeed. Refresh must run exactly once across both parallel calls.
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      if (String(url).includes("/api/auth/refresh")) {
        return jsonResponse(200, { access_token: "fresh-token", refresh_token: "rotated-refresh" });
      }
      const auth = (init?.headers as Record<string, string> | undefined)?.Authorization;
      if (auth === "Bearer fresh-token") return jsonResponse(200, { ok: true });
      return jsonResponse(401, { detail: "expired" });
    });
    vi.stubGlobal("fetch", fetchMock);

    await Promise.all([client.get("/a"), client.get("/b")]);

    const refreshCalls = fetchMock.mock.calls.filter(([u]) => String(u).includes("/api/auth/refresh"));
    expect(refreshCalls).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// GET event stream: the same Bearer/single-flight refresh story plus
// cancellable reader semantics.
// ---------------------------------------------------------------------------

function sseResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return { ok: true, status: 200, body } as Response;
}

async function collect(generator: AsyncGenerator<unknown>): Promise<unknown[]> {
  const out: unknown[] = [];
  for await (const item of generator) out.push(item);
  return out;
}

describe("ApiClient.getEventStream", () => {
  beforeEach(() => {
    localStorage.setItem("access_token", "expired-token");
    localStorage.setItem("refresh_token", "valid-refresh-token");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("drops frames already decoded in the same chunk after abort", async () => {
    const client = new ApiClient();
    const controller = new AbortController();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse(["data: first\n\ndata: second\n\n"])));
    const generator = client.getEventStream("/s", { signal: controller.signal });
    expect((await generator.next()).value).toMatchObject({ data: "first" });
    controller.abort();
    await expect(generator.next()).rejects.toMatchObject({ name: "AbortError" });
  });

  it("rejects invalid UTF-8 instead of accepting replacement characters", async () => {
    const encoder = new TextEncoder();
    const bytes = new Uint8Array([
      ...encoder.encode('data: {"name":"'), 0xc3, 0x28, ...encoder.encode('"}\n\n'),
    ]);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(new ReadableStream({
      start(controller) { controller.enqueue(bytes); controller.close(); },
    }))));
    await expect(collect(new ApiClient().getEventStream("/s"))).rejects.toMatchObject({
      error: { code: "INVALID_STREAM_ENCODING" },
    });
  });

  it("decodes a UTF-8 code point split inside its bytes", async () => {
    const encoder = new TextEncoder();
    const bytes = encoder.encode('data: {"name":"事件"}\n\n');
    const split = bytes.indexOf(0xe4) + 1;
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(new ReadableStream({
      start(controller) {
        controller.enqueue(bytes.slice(0, split));
        controller.enqueue(bytes.slice(split));
        controller.close();
      },
    }))));
    expect(await collect(new ApiClient().getEventStream("/s"))).toEqual([
      { event: null, data: '{"name":"事件"}', terminated: true },
    ]);
  });

  it("shares one refresh between a GET subscription and a JSON request", async () => {
    const client = new ApiClient();
    let release: (response: Response) => void = () => {};
    const refresh = new Promise<Response>((resolve) => { release = resolve; });
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (url === "/api/auth/refresh") return refresh;
      const auth = (init?.headers as Record<string, string> | undefined)?.Authorization;
      if (auth !== "Bearer fresh-token") return Promise.resolve(jsonResponse(401, {}));
      return Promise.resolve(url === "/s" ? sseResponse(["data: ok\n\n"]) : jsonResponse(200, { ok: true }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const stream = collect(client.getEventStream("/s"));
    const json = client.get("/a");
    await vi.waitFor(() => expect(fetchMock.mock.calls.filter(([url]) => url === "/api/auth/refresh")).toHaveLength(1));
    release(jsonResponse(200, { access_token: "fresh-token", refresh_token: "rotated-token" }));
    await expect(Promise.all([stream, json])).resolves.toEqual([
      [{ event: null, data: "ok", terminated: true }], { ok: true },
    ]);
    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/auth/refresh")).toHaveLength(1);
  });

  it("never reconnects a cancelled subscription after a shared refresh completes", async () => {
    const controller = new AbortController();
    let release: (response: Response) => void = () => {};
    const refresh = new Promise<Response>((resolve) => { release = resolve; });
    const fetchMock = vi.fn((url: string) => url === "/api/auth/refresh"
      ? refresh : Promise.resolve(jsonResponse(401, {})));
    vi.stubGlobal("fetch", fetchMock);
    const pending = collect(new ApiClient().getEventStream("/s", { signal: controller.signal }));
    const rejection = expect(pending).rejects.toMatchObject({ name: "AbortError" });
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    controller.abort();
    release(jsonResponse(200, { access_token: "fresh-token", refresh_token: "rotated-token" }));
    await rejection;
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("parses chunked frames through the real stream reader", async () => {
    const client = new ApiClient();
    // Frame boundaries and UTF-8 code points both split across chunks; a
    // line break inside a frame joins data lines, it does not end the frame.
    const fetchMock = vi.fn().mockResolvedValue(
      sseResponse(['data: {"a":', "1}\n", 'data: {"b":2}\n\ndata: {"c":"事件', '"}\n\n']),
    );
    vi.stubGlobal("fetch", fetchMock);

    const frames = await collect(client.getEventStream("/api/runs/run-1/events/stream"));
    expect(frames).toEqual([
      { event: null, data: '{"a":1}\n{"b":2}', terminated: true },
      { event: null, data: '{"c":"事件"}', terminated: true },
    ]);
  });

  it("refreshes a pre-connection 401 and retries with the fresh token, never in the URL", async () => {
    const client = new ApiClient();
    const fetchMock = vi.fn();
    fetchMock.mockResolvedValueOnce(jsonResponse(401, { detail: "expired" }));
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, { access_token: "fresh-token", refresh_token: "rotated-refresh" }),
    );
    fetchMock.mockResolvedValueOnce(sseResponse(["data: ok\n\n"]));
    vi.stubGlobal("fetch", fetchMock);

    const frames = await collect(client.getEventStream("/api/runs/run-1/events/stream"));
    expect(frames).toEqual([{ event: null, data: "ok", terminated: true }]);

    const [url, retryInit] = fetchMock.mock.calls[2] as [string, RequestInit];
    expect(String(url)).not.toContain("fresh-token");
    expect((retryInit.headers as Record<string, string>).Authorization).toBe("Bearer fresh-token");
    expect(localStorage.getItem("access_token")).toBe("fresh-token");
  });

  it("surfaces an unterminated EOF tail as a not-terminated frame", async () => {
    const client = new ApiClient();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(sseResponse(["data: full\n\ndata: half"])),
    );
    const frames = await collect(client.getEventStream("/s"));
    expect(frames).toEqual([
      { event: null, data: "full", terminated: true },
      { event: null, data: "half", terminated: false },
    ]);
  });

  it("throws the JSON error body for a pre-connection rejection", async () => {
    const client = new ApiClient();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(403, { error: { code: "FORBIDDEN", message: "viewer role required", details: null } }),
      ),
    );
    await expect(collect(client.getEventStream("/s"))).rejects.toMatchObject({
      error: { code: "FORBIDDEN" },
    });
  });

  it("stops reading when the consumer aborts mid-stream", async () => {
    const client = new ApiClient();
    const encoder = new TextEncoder();
    const controller = new AbortController();
    // Real fetch tears the body down on abort; the stub mirrors that by
    // erroring the stream when the request signal fires.
    const body = new ReadableStream<Uint8Array>({
      start(streamController) {
        streamController.enqueue(encoder.encode("data: first\n\n"));
        controller.signal.addEventListener("abort", () => {
          streamController.error(new DOMException("Aborted", "AbortError"));
        });
      },
    });
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, body } as Response);
    vi.stubGlobal("fetch", fetchMock);

    const generator = client.getEventStream("/s", { signal: controller.signal });
    const first = await generator.next();
    expect(first.value).toMatchObject({ data: "first", terminated: true });
    controller.abort();
    await expect(generator.next()).rejects.toMatchObject({ name: "AbortError" });
    await expect(generator.next()).resolves.toMatchObject({ done: true });
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect((init.signal as AbortSignal).aborted).toBe(true);
  });
});
