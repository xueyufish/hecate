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
