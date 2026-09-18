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

  async patch<T>(path: string, body: unknown): Promise<T> {
    return this.request<T>(path, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
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

export interface CitationBadge {
  cited: string[];
  unresolved: string[];
}

export interface ReplayTimelineResponse {
  traces: ReplayTraceSegment[];
  unattributed: ReplayEvent[];
  next_cursor: number | null;
  payload_truncated: boolean;
  guardrail_blocks: ReplayGuardrailBlock[];
  message_bodies: Record<string, unknown[]>;
  citation_badges: Record<string, CitationBadge>;
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

// Evaluation report dashboard (7.2e) types and methods.

export interface OverviewReport {
  window_start: string;
  window_end: string;
  quality: {
    offline_pass_rate: number | null;
    offline_runs_counted: number;
    online_avg_score: number | null;
  };
  volume: { completed_runs: number; online_scored: number };
  coverage: {
    active_datasets: number;
    median_items: number | null;
    low_sample_run_ratio: number;
  };
  error_rate: { total_scores: number; error_count: number; ratio: number };
}

export interface TrendPoint {
  bucket: string;
  value: number;
  count: number;
}

export interface TrendSeries {
  group_id: string;
  kind: "offline" | "online";
  metric: string;
  points: TrendPoint[];
}

export interface TrendsReport {
  dimension: string;
  bucket: string;
  series: TrendSeries[];
}

export interface HistogramBin {
  lower: number;
  upper: number;
  count: number;
}

export interface MetricDistribution {
  metric_name: string;
  bins: HistogramBin[];
  count: number;
  error_count: number;
  mean: number | null;
  min: number | null;
  max: number | null;
}

export interface DistributionsReport {
  scope: "run" | "task";
  scope_id: string;
  metrics: MetricDistribution[];
}

export interface BreakdownMetric {
  metric_name: string;
  avg: number;
  count: number;
}

export interface BreakdownGroup {
  key: string;
  metrics: BreakdownMetric[];
}

export interface BreakdownsReport {
  group_by: string;
  groups: BreakdownGroup[];
  total: number;
}

export interface SessionRollupItem {
  session_id: string;
  agent_id: string | null;
  trace_count: number;
  last_scored_at: string;
  metrics: BreakdownMetric[];
}

export interface SessionRollupReport {
  items: SessionRollupItem[];
  total: number;
}

export interface RunListItem {
  id: string;
  dataset_id: string;
  status: string;
  summary: Record<string, unknown> | null;
  created_at: string;
  completed_at: string | null;
}

export interface RunScoreItem {
  id: string;
  run_id: string;
  item_id: string;
  metric_name: string;
  value: number;
  reasoning: string | null;
  source: string;
  created_at: string;
}

export interface RunCompareMetric {
  metric: string;
  baseline_avg: number;
  candidate_avg: number;
  delta: number;
  is_regression: boolean;
}

export interface RunCompareResult {
  baseline_run_id: string;
  candidate_run_id: string;
  metrics: RunCompareMetric[];
  token_usage_delta: number;
  latency_delta_ms: number;
  cost_delta: number | null;
  dataset_drift: { changed_item_ids: string[] } | null;
  node_drift: { session_id: string }[] | null;
  overall_regressed: boolean;
}

export interface OnlineTaskListItem {
  id: string;
  name: string;
  status: string;
  metrics: { scanned?: number; sampled?: number; scored?: number; errors?: number };
  config: { sampling_rate?: number; max_traces_per_cycle?: number; agent_id?: string };
}

export interface AnnotationMetricDef {
  name: string;
  data_type: "numeric" | "categorical" | "boolean";
  min?: number;
  max?: number;
  categories?: string[];
}

export interface AnnotationQueue {
  id: string;
  name: string;
  description: string | null;
  instructions: string | null;
  metric_defs: AnnotationMetricDef[];
  assigned_user_ids: string[];
  workspace_id: string;
  created_at: string;
  updated_at: string;
  counts?: Record<string, number>;
}

export interface AnnotationQueueItem {
  id: string;
  queue_id: string;
  target_type: string;
  target_id: string;
  status: "pending" | "claimed" | "completed" | "skipped";
  added_by: string | null;
  claimed_by: string | null;
  claimed_at: string | null;
  completed_by: string | null;
  completed_at: string | null;
  created_at: string;
}

export interface AnnotationSuggestion {
  metric_name: string;
  value: number;
  source: string;
  reasoning: string | null;
  score_id: string;
}

export interface AnnotationItemDetail {
  item: AnnotationQueueItem;
  queue: AnnotationQueue;
  trace: {
    id: string;
    trace_id: string;
    session_id: string | null;
    status: string;
    start_time: string;
    end_time: string | null;
  } | null;
  projection: {
    messages: { role: string; content: string }[];
    tool_calls: { name: string; args: unknown; tool_call_id: string }[];
  } | null;
  suggestions: AnnotationSuggestion[];
}

export interface AnnotationEntryPayload {
  metric_name: string;
  value?: number;
  value_label?: string;
  overrides_score_id?: string;
  reason_code?: string;
  justification?: string;
}

export interface CalibrationHeatmapCell {
  machine_bin: number;
  human_bin: number;
  count: number;
}

export interface CalibrationMetric {
  metric_name: string;
  pair_count: number;
  agreement_rate: number | null;
  mae: number | null;
  kappa: number | null;
  data_mode: "numeric" | "categorical";
  machine_only_count: number;
  human_only_count: number;
  heatmap: CalibrationHeatmapCell[];
}

export interface CalibrationReport {
  metrics: CalibrationMetric[];
}

export interface SessionTrace {
  id: string;
  trace_id: string;
  session_id: string | null;
  status: string;
  start_time: string;
}

/** 7.3b: a frozen, named snapshot of a dataset's item set. */
export interface DatasetVersion {
  id: string;
  dataset_id: string;
  name: string;
  description: string | null;
  items: EvaluationItem[];
  content_hash: string;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface DatasetVersionListEntry {
  id: string;
  dataset_id: string;
  name: string;
  description: string | null;
  content_hash: string;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface DatasetVersionDiffResult {
  base: { kind: string; version_id: string; name: string; content_hash: string };
  target: {
    kind: string;
    version_id?: string;
    name?: string;
    content_hash: string;
  };
  added: Array<Record<string, unknown>>;
  removed: Array<Record<string, unknown>>;
  changed: Array<{ item_id: string; fields: Record<string, { base: unknown; target: unknown }> }>;
}

export interface EvaluationItem {
  id: string;
  dataset_id: string;
  query: string;
  expected_answer: string | null;
  generated_answer: string | null;
  context: string[] | null;
  tags: string[] | null;
  known_bad: boolean;
  known_bad_reason: string | null;
  known_bad_marked_by: string | null;
  known_bad_marked_at: string | null;
  known_bad_expires_at: string | null;
  created_at: string;
}

export interface EvaluationGateConfig {
  mode: "off" | "warn" | "require";
  min_pass_rate?: number | null;
  block_on_regression?: boolean;
  block_on_drift?: boolean;
  require_run?: boolean;
  require_dataset_version?: boolean;
}

export interface EvaluationApi {
  getOverview(window?: { start_date?: string; end_date?: string }): Promise<OverviewReport>;
  getTrends(params: {
    dimension: string;
    bucket?: string;
    metric_name?: string;
    start_date?: string;
    end_date?: string;
  }): Promise<TrendsReport>;
  getDistributions(params: { run_id?: string; task_id?: string; metric_name?: string }): Promise<DistributionsReport>;
  getBreakdowns(params: {
    group_by: string;
    metric_name?: string;
    start_date?: string;
    end_date?: string;
  }): Promise<BreakdownsReport>;
  getSessions(params: {
    task_id?: string;
    start_date?: string;
    end_date?: string;
    page?: number;
    page_size?: number;
  }): Promise<SessionRollupReport>;
  listRuns(): Promise<{ items: RunListItem[]; total: number }>;
  listRunScores(runId: string): Promise<{ items: RunScoreItem[]; total: number }>;
  listOnlineTasks(): Promise<{ items: OnlineTaskListItem[]; total: number }>;
  compareRuns(baselineRunId: string, candidateRunId: string): Promise<RunCompareResult>;
  // Annotation queues (7.4) + calibration (7.4a)
  listAnnotationQueues(): Promise<{ items: AnnotationQueue[]; total: number }>;
  createAnnotationQueue(payload: {
    name: string;
    instructions?: string;
    metric_defs: AnnotationMetricDef[];
  }): Promise<AnnotationQueue>;
  deleteAnnotationQueue(queueId: string): Promise<void>;
  listAnnotationQueueItems(queueId: string, status?: string): Promise<{ items: AnnotationQueueItem[]; total: number }>;
  addAnnotationQueueItems(
    queueId: string,
    targetIds: string[]
  ): Promise<{ created: number; already_present: string[]; rejected: { target_id: string; reason: string }[] }>;
  addAnnotationQueueItemsFromTask(
    queueId: string,
    payload: { task_id: string; metric_name?: string; min_score?: number; max_score?: number; limit?: number }
  ): Promise<{ created: number; skipped_existing: number; candidates: number; limit: number }>;
  getAnnotationQueueItemDetail(queueId: string, itemId: string): Promise<AnnotationItemDetail>;
  claimAnnotationQueueItem(queueId: string, itemId: string): Promise<AnnotationQueueItem>;
  skipAnnotationQueueItem(queueId: string, itemId: string): Promise<AnnotationQueueItem>;
  submitAnnotationQueueItem(
    queueId: string,
    itemId: string,
    annotations: AnnotationEntryPayload[]
  ): Promise<AnnotationQueueItem>;
  pushAnnotationQueueToDataset(
    queueId: string,
    payload: { dataset_id?: string; dataset_name?: string; item_ids?: string[] }
  ): Promise<{ created: number; skipped: number; dataset_id: string }>;
  getCalibration(params?: { start_date?: string; end_date?: string; metric_name?: string }): Promise<CalibrationReport>;
  listSessionTraces(sessionId: string): Promise<SessionTrace[]>;
  // 7.3b: dataset versions
  listDatasetVersions(
    datasetId: string,
    params?: { page?: number; page_size?: number }
  ): Promise<{ items: DatasetVersionListEntry[]; total: number }>;
  createDatasetVersion(
    datasetId: string,
    payload: { name: string; description?: string | null }
  ): Promise<DatasetVersion>;
  getDatasetVersion(datasetId: string, versionId: string): Promise<DatasetVersion>;
  deleteDatasetVersion(datasetId: string, versionId: string): Promise<void>;
  checkoutDatasetVersion(
    datasetId: string,
    versionId: string
  ): Promise<{ added: number; removed: number; changed: number; live_items_after: number }>;
  diffDatasetVersion(
    datasetId: string,
    versionId: string,
    against: string
  ): Promise<DatasetVersionDiffResult>;
  // 7.3c: known-bad item marking UI
  markItemKnownBad(
    datasetId: string,
    itemId: string,
    payload: { known_bad: boolean; known_bad_reason?: string | null; known_bad_expires_at?: string | null }
  ): Promise<EvaluationItem>;
  // 7.3a: publish evaluation gate
  updateWorkflowGate(workflowId: string, gate: EvaluationGateConfig | null): Promise<unknown>;
  publishWorkflowVersion(
    workflowId: string,
    version: number,
    body?: { force?: boolean }
  ): Promise<unknown>;
}

export const evaluationApi: EvaluationApi = {
  async getOverview(window = {}) {
    const params = new URLSearchParams(
      Object.entries(window).filter(([, v]) => v !== undefined) as [string, string][]
    );
    return api.get<OverviewReport>(`/api/evaluation/reports/overview?${params}`);
  },
  async getTrends(params) {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined) as [string, string][]
    );
    return api.get<TrendsReport>(`/api/evaluation/reports/trends?${qs}`);
  },
  async getDistributions(params) {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined) as [string, string][]
    );
    return api.get<DistributionsReport>(`/api/evaluation/reports/distributions?${qs}`);
  },
  async getBreakdowns(params) {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined) as [string, string][]
    );
    return api.get<BreakdownsReport>(`/api/evaluation/reports/breakdowns?${qs}`);
  },
  async getSessions(params) {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined) as [string, string][]
    );
    return api.get<SessionRollupReport>(`/api/evaluation/reports/sessions?${qs}`);
  },
  async listRuns() {
    return api.get<{ items: RunListItem[]; total: number }>(`/api/evaluation/runs?page_size=100`);
  },
  async listRunScores(runId) {
    return api.get<{ items: RunScoreItem[]; total: number }>(
      `/api/evaluation/runs/${runId}/scores?page_size=100`
    );
  },
  async listOnlineTasks() {
    return api.get<{ items: OnlineTaskListItem[]; total: number }>(
      `/api/evaluation/tasks?task_type=online&page_size=100`
    );
  },
  async compareRuns(baselineRunId, candidateRunId) {
    return api.post<RunCompareResult>(`/api/evaluation/runs/compare`, {
      baseline_run_id: baselineRunId,
      candidate_run_id: candidateRunId,
    });
  },
  async listAnnotationQueues() {
    return api.get<{ items: AnnotationQueue[]; total: number }>(`/api/evaluation/annotation-queues?page_size=100`);
  },
  async createAnnotationQueue(payload) {
    return api.post<AnnotationQueue>(`/api/evaluation/annotation-queues`, payload);
  },
  async deleteAnnotationQueue(queueId) {
    await api.delete(`/api/evaluation/annotation-queues/${queueId}`);
  },
  async listAnnotationQueueItems(queueId, status) {
    const qs = new URLSearchParams(status ? { status } : {});
    return api.get<{ items: AnnotationQueueItem[]; total: number }>(
      `/api/evaluation/annotation-queues/${queueId}/items?${qs}`
    );
  },
  async addAnnotationQueueItems(queueId, targetIds) {
    return api.post(`/api/evaluation/annotation-queues/${queueId}/items`, { target_ids: targetIds });
  },
  async addAnnotationQueueItemsFromTask(queueId, payload) {
    return api.post(`/api/evaluation/annotation-queues/${queueId}/items/from-task`, payload);
  },
  async getAnnotationQueueItemDetail(queueId, itemId) {
    return api.get<AnnotationItemDetail>(`/api/evaluation/annotation-queues/${queueId}/items/${itemId}`);
  },
  async claimAnnotationQueueItem(queueId, itemId) {
    return api.post<AnnotationQueueItem>(`/api/evaluation/annotation-queues/${queueId}/items/${itemId}/claim`);
  },
  async skipAnnotationQueueItem(queueId, itemId) {
    return api.post<AnnotationQueueItem>(`/api/evaluation/annotation-queues/${queueId}/items/${itemId}/skip`);
  },
  async submitAnnotationQueueItem(queueId, itemId, annotations) {
    return api.post<AnnotationQueueItem>(`/api/evaluation/annotation-queues/${queueId}/items/${itemId}/submit`, {
      annotations,
    });
  },
  async pushAnnotationQueueToDataset(queueId, payload) {
    return api.post(`/api/evaluation/annotation-queues/${queueId}/push-dataset`, payload);
  },
  async getCalibration(params = {}) {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined) as [string, string][]
    );
    return api.get<CalibrationReport>(`/api/evaluation/calibration?${qs}`);
  },
  async listSessionTraces(sessionId) {
    return api.get<SessionTrace[]>(`/api/traces?session_id=${sessionId}&limit=50`);
  },
  // 7.3b: dataset versions
  async listDatasetVersions(datasetId, params = {}) {
    const qs = new URLSearchParams(
      Object.entries(params)
        .filter(([, v]) => v !== undefined)
        .map(([k, v]) => [k, String(v)]) as [string, string][]
    );
    return api.get<{ items: DatasetVersionListEntry[]; total: number }>(
      `/api/evaluation/datasets/${datasetId}/versions?${qs}`
    );
  },
  async createDatasetVersion(datasetId, payload) {
    return api.post<DatasetVersion>(
      `/api/evaluation/datasets/${datasetId}/versions`,
      payload,
    );
  },
  async getDatasetVersion(datasetId, versionId) {
    return api.get<DatasetVersion>(
      `/api/evaluation/datasets/${datasetId}/versions/${versionId}`,
    );
  },
  async deleteDatasetVersion(datasetId, versionId) {
    return api.delete(
      `/api/evaluation/datasets/${datasetId}/versions/${versionId}`,
    );
  },
  async checkoutDatasetVersion(datasetId, versionId) {
    return api.post<{ added: number; removed: number; changed: number; live_items_after: number }>(
      `/api/evaluation/datasets/${datasetId}/versions/${versionId}/checkout`,
    );
  },
  async diffDatasetVersion(datasetId, versionId, against) {
    return api.get<DatasetVersionDiffResult>(
      `/api/evaluation/datasets/${datasetId}/versions/${versionId}/diff?against=${encodeURIComponent(against)}`,
    );
  },
  // 7.3c: known-bad item marking
  async markItemKnownBad(datasetId, itemId, payload) {
    return api.patch<EvaluationItem>(
      `/api/evaluation/datasets/${datasetId}/items/${itemId}`,
      payload,
    );
  },
  // 7.3a: publish evaluation gate
  async updateWorkflowGate(workflowId, gate) {
    return api.put<unknown>(`/api/workflows/${workflowId}`, { evaluation_gate: gate });
  },
  async publishWorkflowVersion(workflowId, version, body) {
    return api.post<unknown>(
      `/api/workflows/${workflowId}/publish/${version}`,
      body,
    );
  },
};

// Agent versioning & channel publishing (1.3.20).

import type {
  AgentVersion,
  AgentVersionDrift,
  AgentVersionStatus,
  ChannelEntry,
  VersionDiff,
} from "./api-types";

export const agentVersionsApi = {
  list: (agentId: string) => api.get<{ items: AgentVersion[]; total: number }>(`/api/agents/${agentId}/versions`),
  get: (agentId: string, version: number) => api.get<AgentVersion>(`/api/agents/${agentId}/versions/${version}`),
  status: (agentId: string) => api.get<AgentVersionStatus>(`/api/agents/${agentId}/version-status`),
  commit: (agentId: string, body: { name?: string; change_summary?: string }) =>
    api.post<AgentVersion>(`/api/agents/${agentId}/versions/commit`, body),
  publish: (agentId: string, version: number, force = false) =>
    api.post<AgentVersion>(`/api/agents/${agentId}/publish/${version}`, force ? { force: true } : {}),
  rollback: (agentId: string, version: number) =>
    api.post<AgentVersion>(`/api/agents/${agentId}/rollback/${version}`),
  diff: (agentId: string, v1: number, v2: number) =>
    api.get<VersionDiff>(`/api/agents/${agentId}/diff?v1=${v1}&v2=${v2}`),
  drift: (agentId: string, version: number) =>
    api.get<AgentVersionDrift>(`/api/agents/${agentId}/versions/${version}/drift`),
  rename: (agentId: string, version: number, body: { name?: string; change_summary?: string }) =>
    api.patch<AgentVersion>(`/api/agents/${agentId}/versions/${version}`, body),
  remove: (agentId: string, version: number) =>
    api.delete<void>(`/api/agents/${agentId}/versions/${version}`),
};

export interface ChannelCreateInput {
  name: string;
  type: ChannelEntry["type"];
  agent_id: string;
  bind_mode?: ChannelEntry["bind_mode"];
  pinned_version?: number | null;
  config?: Record<string, unknown>;
}

export const channelsApi = {
  list: (agentId?: string) =>
    api.get<ChannelEntry[]>(`/api/channels${agentId ? `?agent_id=${agentId}` : ""}`),
  get: (channelId: string) => api.get<ChannelEntry>(`/api/channels/${channelId}`),
  create: (body: ChannelCreateInput) => api.post<ChannelEntry>("/api/channels", body),
  update: (
    channelId: string,
    body: {
      name?: string;
      bind_mode?: ChannelEntry["bind_mode"];
      pinned_version?: number | null;
      config?: Record<string, unknown>;
      status?: ChannelEntry["status"];
    }
  ) => api.put<ChannelEntry>(`/api/channels/${channelId}`, body),
  remove: (channelId: string) => api.delete<void>(`/api/channels/${channelId}`),
};
