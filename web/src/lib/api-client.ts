const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

export interface ApiError {
  error: { code: string; message: string; details: unknown };
}

class ApiClient {
  private baseUrl: string;

  constructor(baseUrl: string = API_BASE) {
    this.baseUrl = baseUrl;
  }

  private getToken(): string | null {
    if (typeof window === "undefined") return null;
    return localStorage.getItem("access_token");
  }

  private getRefreshToken(): string | null {
    if (typeof window === "undefined") return null;
    return localStorage.getItem("refresh_token");
  }

  private async refreshIfNeeded(response: Response): Promise<string | null> {
    if (response.status !== 401) return null;
    const refreshToken = this.getRefreshToken();
    if (!refreshToken) return null;

    try {
      const res = await fetch(`${this.baseUrl}/api/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
      if (!res.ok) return null;
      const data = await res.json();
      localStorage.setItem("access_token", data.access_token);
      localStorage.setItem("refresh_token", data.refresh_token);
      return data.access_token;
    } catch {
      return null;
    }
  }

  private async request<T>(
    path: string,
    options: RequestInit = {}
  ): Promise<T> {
    const token = this.getToken();
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      ...(options.headers as Record<string, string>),
    };
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }

    const response = await fetch(`${this.baseUrl}${path}`, {
      ...options,
      headers,
    });

    if (response.status === 401) {
      const newToken = await this.refreshIfNeeded(response);
      if (newToken) {
        headers["Authorization"] = `Bearer ${newToken}`;
        const retry = await fetch(`${this.baseUrl}${path}`, {
          ...options,
          headers,
        });
        if (!retry.ok) {
          const err: ApiError = await retry.json().catch(() => ({
            error: { code: "UNKNOWN", message: retry.statusText, details: null },
          }));
          throw err;
        }
        return retry.json();
      }
      if (typeof window !== "undefined") {
        localStorage.removeItem("access_token");
        localStorage.removeItem("refresh_token");
        window.location.href = "/login";
      }
      throw { error: { code: "UNAUTHORIZED", message: "Please log in again", details: null } };
    }

    if (!response.ok) {
      const err: ApiError = await response.json().catch(() => ({
        error: { code: "UNKNOWN", message: response.statusText, details: null },
      }));
      throw err;
    }

    return response.json();
  }

  async get<T>(path: string): Promise<T> {
    return this.request<T>(path);
  }

  async post<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>(path, {
      method: "POST",
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
  }

  async put<T>(path: string, body: unknown): Promise<T> {
    return this.request<T>(path, {
      method: "PUT",
      body: JSON.stringify(body),
    });
  }

  async delete<T>(path: string): Promise<T> {
    return this.request<T>(path, { method: "DELETE" });
  }

  async *stream(
    path: string,
    body: {
      model: string;
      messages: { role: string; content: string }[];
      stream?: boolean;
      kb_ids?: string[];
      session_id?: string;
    }
  ): AsyncGenerator<string> {
    const token = this.getToken();
    const response = await fetch(`${this.baseUrl}${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ ...body, stream: true }),
    });

    if (!response.ok || !response.body) {
      throw new Error(`Stream error: ${response.status}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const data = line.slice(6).trim();
        if (data === "[DONE]") return;
        try {
          const parsed = JSON.parse(data);
          const content = parsed.choices?.[0]?.delta?.content;
          if (content) yield content;
        } catch {
          // skip malformed SSE lines
        }
      }
    }
  }

  async upload(path: string, file: File): Promise<unknown> {
    const token = this.getToken();
    const formData = new FormData();
    formData.append("file", file);
    const response = await fetch(`${this.baseUrl}${path}`, {
      method: "POST",
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: formData,
    });
    if (!response.ok) {
      const err: ApiError = await response.json().catch(() => ({
        error: { code: "UNKNOWN", message: response.statusText, details: null },
      }));
      throw err;
    }
    return response.json();
  }
}

export const api = new ApiClient();

// Execution replay (8.20) types and methods.

export interface ReplayEvent {
  event_type: string;
  superstep: number;
  node_id: string | null;
  timestamp: string;
  version: number;
  payload: Record<string, unknown>;
}

export interface ReplayTraceSegment {
  trace_id: string;
  event_count: number;
  first_version: number;
  events: ReplayEvent[];
}

export interface ReplayTraceEnrichment {
  status?: string;
  usage?: Record<string, unknown> | null;
  total_latency_ms?: number | null;
  ttft_ms?: number | null;
  span_name?: string;
}

export interface ReplayGuardrailBlock {
  version: number;
  node_id: string | null;
  superstep: number;
  reason: string;
  block_type: string;
}

export interface ReplayTimelineResponse {
  traces: ReplayTraceSegment[];
  unattributed: ReplayEvent[];
  next_cursor: number | null;
  payload_truncated: boolean;
  guardrail_blocks: ReplayGuardrailBlock[];
  message_bodies: Record<string, unknown[]>;
  trace_enrichment: Record<string, ReplayTraceEnrichment>;
  payload_preview_chars: number;
}

export interface ReplayStateResponse {
  effective_version: number;
  requested_version: number;
  channel_state: Record<string, unknown>;
  messages: unknown[];
  commit_points: number[];
  fell_back: boolean;
}

export interface SessionDetail {
  id: string;
  agent_id: string;
  status: string;
  log_version: number;
  [k: string]: unknown;
}

export interface ReplayApi {
  getReplayTimeline(
    sessionId: string,
    opts?: { fromVersion?: number; limit?: number; detail?: boolean }
  ): Promise<ReplayTimelineResponse>;
  getReplayState(sessionId: string, atVersion: number): Promise<ReplayStateResponse>;
  getSession(sessionId: string): Promise<SessionDetail>;
}

export const replayApi: ReplayApi = {
  async getReplayTimeline(sessionId, opts = {}) {
    const params = new URLSearchParams();
    if (opts.fromVersion !== undefined) params.set("from_version", String(opts.fromVersion));
    if (opts.limit !== undefined) params.set("limit", String(opts.limit));
    if (opts.detail) params.set("detail", "true");
    const qs = params.toString();
    return api.get<ReplayTimelineResponse>(
      `/api/sessions/${sessionId}/replay${qs ? `?${qs}` : ""}`
    );
  },
  async getReplayState(sessionId, atVersion) {
    return api.get<ReplayStateResponse>(
      `/api/sessions/${sessionId}/replay/state?at_version=${atVersion}`
    );
  },
  async getSession(sessionId) {
    return api.get<SessionDetail>(`/api/sessions/${sessionId}`);
  },
};
