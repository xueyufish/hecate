import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OverviewView } from "./overview-view";
import { RunReportView } from "./run-report-view";
import { CompareView } from "./compare-view";
import { AnnotationsView } from "./annotations-view";
import { CalibrationView } from "./calibration-view";
import EvaluationPage from "@/app/(dashboard)/ops-center/evaluation/page";

const evaluationApiMock = vi.hoisted(() => ({
  getOverview: vi.fn(),
  getTrends: vi.fn(),
  getDistributions: vi.fn(),
  getBreakdowns: vi.fn(),
  getSessions: vi.fn(),
  listRuns: vi.fn(),
  listRunScores: vi.fn(),
  listOnlineTasks: vi.fn(),
  compareRuns: vi.fn(),
  listAnnotationQueues: vi.fn(),
  createAnnotationQueue: vi.fn(),
  deleteAnnotationQueue: vi.fn(),
  listAnnotationQueueItems: vi.fn(),
  addAnnotationQueueItems: vi.fn(),
  addAnnotationQueueItemsFromTask: vi.fn(),
  getAnnotationQueueItemDetail: vi.fn(),
  claimAnnotationQueueItem: vi.fn(),
  skipAnnotationQueueItem: vi.fn(),
  submitAnnotationQueueItem: vi.fn(),
  pushAnnotationQueueToDataset: vi.fn(),
  getCalibration: vi.fn(),
  listSessionTraces: vi.fn(),
}));

vi.mock("@/lib/api-client", () => ({
  api: { get: vi.fn(), post: vi.fn() },
  evaluationApi: evaluationApiMock,
}));

const ZERO_OVERVIEW = {
  window_start: "2026-09-01T00:00:00Z",
  window_end: "2026-09-11T00:00:00Z",
  quality: { offline_pass_rate: null, offline_runs_counted: 0, online_avg_score: null },
  volume: { completed_runs: 0, online_scored: 0 },
  coverage: { active_datasets: 0, median_items: null, low_sample_run_ratio: 0 },
  error_rate: { total_scores: 0, error_count: 0, ratio: 0 },
};

const DATA_OVERVIEW = {
  ...ZERO_OVERVIEW,
  quality: { offline_pass_rate: 0.8, offline_runs_counted: 2, online_avg_score: 0.75 },
  volume: { completed_runs: 2, online_scored: 3 },
  coverage: { active_datasets: 3, median_items: 50, low_sample_run_ratio: 0.3333 },
  error_rate: { total_scores: 5, error_count: 1, ratio: 0.2 },
};

describe("OverviewView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    evaluationApiMock.getTrends.mockResolvedValue({ dimension: "dataset", bucket: "day", series: [] });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the four summary cards with data", async () => {
    render(
      <OverviewView overview={DATA_OVERVIEW} startDate="2026-09-01" endDate="2026-09-11" />
    );

    await waitFor(() => {
      expect(screen.getByText("80.0%")).toBeInTheDocument();
    });
    expect(screen.getByText("Coverage")).toBeInTheDocument();
    expect(screen.getByText("33%")).toBeInTheDocument();
    expect(screen.getByText("20.0%")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
  });
});

describe("EvaluationPage empty state", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    evaluationApiMock.getOverview.mockResolvedValue(ZERO_OVERVIEW);
    // Annotation/Calibration views mount with the page and fetch on their own.
    evaluationApiMock.listAnnotationQueues.mockResolvedValue({ items: [], total: 0 });
    evaluationApiMock.getCalibration.mockResolvedValue({ metrics: [] });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the no-data guidance for data views while tabs stay reachable", async () => {
    render(<EvaluationPage />);

    await waitFor(() => {
      expect(screen.getByText("No evaluation data")).toBeInTheDocument();
    });
    // Six views are reachable even with no evaluation data (7.4).
    expect(screen.getByRole("button", { name: "Annotation" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Calibration" })).toBeInTheDocument();
  });
});

describe("RunReportView", () => {
  const RUN_ID = "11111111-1111-1111-1111-111111111111";

  beforeEach(() => {
    vi.clearAllMocks();
    evaluationApiMock.listRuns.mockResolvedValue({
      items: [{ id: RUN_ID, dataset_id: "d", status: "completed", summary: null, created_at: "2026-09-05T10:00:00Z", completed_at: null }],
      total: 1,
    });
    evaluationApiMock.getDistributions.mockResolvedValue({
      scope: "run",
      scope_id: RUN_ID,
      metrics: [
        {
          metric_name: "faithfulness",
          bins: Array.from({ length: 10 }, (_, i) => ({ lower: i / 10, upper: (i + 1) / 10, count: i === 9 ? 1 : 0 })),
          count: 1,
          error_count: 0,
          mean: 0.95,
          min: 0.95,
          max: 0.95,
        },
      ],
    });
    evaluationApiMock.listRunScores.mockResolvedValue({
      items: [
        {
          id: "score-1",
          run_id: RUN_ID,
          item_id: "aaaaaaaa-1111-1111-1111-111111111111",
          metric_name: "faithfulness",
          value: 0.2,
          reasoning: "Answer contradicts the provided context.",
          source: "llm_judge",
          created_at: "2026-09-05T10:00:00Z",
        },
      ],
      total: 1,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("expands a low-score row to reveal reasoning and source badge", async () => {
    const user = userEvent.setup();
    render(<RunReportView startDate="2026-09-01" endDate="2026-09-11" />);

    await waitFor(() => {
      expect(screen.getByText("Low Scores")).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.getByText("20%")).toBeInTheDocument();
    });
    expect(screen.queryByText(/contradicts the provided context/i)).not.toBeInTheDocument();

    await user.click(screen.getByText("20%"));

    expect(await screen.findByText("Answer contradicts the provided context.")).toBeInTheDocument();
    expect(screen.getByText("llm_judge")).toBeInTheDocument();
  });
});

describe("CompareView", () => {
  const BASELINE = "22222222-2222-2222-2222-222222222222";
  const CANDIDATE = "33333333-3333-3333-3333-333333333333";

  beforeEach(() => {
    vi.clearAllMocks();
    evaluationApiMock.listRuns.mockResolvedValue({
      items: [
        { id: BASELINE, dataset_id: "d", status: "completed", summary: null, created_at: "2026-09-04T10:00:00Z", completed_at: null },
        { id: CANDIDATE, dataset_id: "d", status: "completed", summary: null, created_at: "2026-09-05T10:00:00Z", completed_at: null },
      ],
      total: 2,
    });
    evaluationApiMock.compareRuns.mockResolvedValue({
      baseline_run_id: BASELINE,
      candidate_run_id: CANDIDATE,
      metrics: [
        { metric: "faithfulness", baseline_avg: 0.7, candidate_avg: 0.8, delta: 0.1, is_regression: false },
      ],
      token_usage_delta: 120,
      latency_delta_ms: -50,
      cost_delta: null,
      dataset_drift: null,
      node_drift: null,
      overall_regressed: false,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders metric deltas after comparing two runs", async () => {
    const user = userEvent.setup();
    render(<CompareView />);

    // Both selects mount all run options (hidden until opened), so queries
    // must be scoped to one select's wrapper div.
    const baselineSelect = screen.getByText("Baseline run").closest<HTMLElement>("div.relative")!;
    const candidateSelect = screen.getByText("Candidate run").closest<HTMLElement>("div.relative")!;
    await user.click(within(baselineSelect).getByText("Baseline run"));
    await user.click(
      within(baselineSelect).getByRole("option", { name: new RegExp(BASELINE.slice(0, 8)) })
    );
    await user.click(within(candidateSelect).getByText("Candidate run"));
    await user.click(
      within(candidateSelect).getByRole("option", { name: new RegExp(CANDIDATE.slice(0, 8)) })
    );
    await user.click(screen.getByRole("button", { name: "Compare" }));

    expect(await screen.findByText("Per-Metric Deltas")).toBeInTheDocument();
    expect(screen.getByText("70.0% → 80.0%")).toBeInTheDocument();
    expect(screen.getByText("+10.0pp")).toBeInTheDocument();
    expect(screen.getByText("Token usage delta")).toBeInTheDocument();
  });
});

const QUEUE = {
  id: "44444444-4444-4444-4444-444444444444",
  name: "weekly-review",
  description: null,
  instructions: "Rate the output",
  metric_defs: [
    { name: "helpfulness", data_type: "numeric", min: 0, max: 1 },
    { name: "tone", data_type: "categorical", categories: ["good", "neutral", "bad"] },
  ],
  assigned_user_ids: [],
  workspace_id: "00000000-0000-0000-0000-0000000000aa",
  created_at: "2026-09-10T12:00:00Z",
  updated_at: "2026-09-10T12:00:00Z",
  counts: { pending: 1, claimed: 0, completed: 0, skipped: 0 },
};

const QUEUE_ITEM = {
  id: "55555555-5555-5555-5555-555555555555",
  queue_id: QUEUE.id,
  target_type: "trace",
  target_id: "66666666-6666-6666-6666-666666666666",
  status: "pending",
  added_by: null,
  claimed_by: null,
  claimed_at: null,
  completed_by: null,
  completed_at: null,
  created_at: "2026-09-10T12:00:00Z",
};

const ITEM_DETAIL = {
  item: QUEUE_ITEM,
  queue: QUEUE,
  trace: {
    id: QUEUE_ITEM.target_id,
    trace_id: QUEUE_ITEM.target_id,
    session_id: "77777777-7777-7777-7777-777777777777",
    status: "completed",
    start_time: "2026-09-10T12:00:00Z",
    end_time: "2026-09-10T12:00:05Z",
  },
  projection: {
    messages: [
      { role: "user", content: "what is X?" },
      { role: "assistant", content: "X is a thing" },
    ],
    tool_calls: [{ name: "search", args: { q: "X" }, tool_call_id: "t1" }],
  },
  suggestions: [
    { metric_name: "helpfulness", value: 0.35, source: "llm_judge", reasoning: "too strict", score_id: "s1" },
  ],
};

describe("AnnotationsView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    evaluationApiMock.listAnnotationQueues.mockResolvedValue({ items: [QUEUE], total: 1 });
    evaluationApiMock.listAnnotationQueueItems.mockResolvedValue({ items: [QUEUE_ITEM], total: 1 });
    evaluationApiMock.claimAnnotationQueueItem.mockResolvedValue({ ...QUEUE_ITEM, status: "claimed" });
    evaluationApiMock.getAnnotationQueueItemDetail.mockResolvedValue(ITEM_DETAIL);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders queue list with per-state counts and metric chips", async () => {
    render(<AnnotationsView />);

    await waitFor(() => {
      expect(screen.getByText("weekly-review")).toBeInTheDocument();
    });
    expect(screen.getByText(/pending 1/)).toBeInTheDocument();
    expect(screen.getByText("helpfulness · tone")).toBeInTheDocument();
  });

  it("prefills numeric annotation with the machine suggestion and submits", async () => {
    const user = userEvent.setup();
    evaluationApiMock.submitAnnotationQueueItem.mockResolvedValue({ ...QUEUE_ITEM, status: "completed" });
    render(<AnnotationsView />);

    await waitFor(() => {
      expect(screen.getByText(/machine: 0.35/)).toBeInTheDocument();
    });
    // The numeric input is prefilled with the suggestion value.
    const input = screen.getByDisplayValue("0.35");
    await user.click(screen.getByRole("button", { name: "Submit" }));

    expect(evaluationApiMock.submitAnnotationQueueItem).toHaveBeenCalledWith(QUEUE.id, QUEUE_ITEM.id, [
      expect.objectContaining({ metric_name: "helpfulness", value: 0.35 }),
      expect.objectContaining({ metric_name: "tone", value_label: "" }),
    ]);
    expect(input).toBeInTheDocument();
  });
});

describe("CalibrationView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders per-metric calibration cards", async () => {
    evaluationApiMock.getCalibration.mockResolvedValue({
      metrics: [
        {
          metric_name: "helpfulness",
          pair_count: 10,
          agreement_rate: 0.9,
          mae: 0.055,
          kappa: null,
          data_mode: "numeric",
          machine_only_count: 2,
          human_only_count: 1,
          heatmap: [{ machine_bin: 9, human_bin: 9, count: 4 }],
        },
      ],
    });
    render(<CalibrationView startDate="2026-09-01" endDate="2026-09-11" />);

    await waitFor(() => {
      expect(screen.getByText("helpfulness")).toBeInTheDocument();
    });
    expect(screen.getByText("90.0%")).toBeInTheDocument();
    expect(screen.getByText("0.0550")).toBeInTheDocument();
    expect(screen.getByText("10")).toBeInTheDocument();
  });

  it("shows the empty state without paired samples", async () => {
    evaluationApiMock.getCalibration.mockResolvedValue({ metrics: [] });
    render(<CalibrationView startDate="2026-09-01" endDate="2026-09-11" />);

    await waitFor(() => {
      expect(screen.getByText("No paired machine-human samples")).toBeInTheDocument();
    });
  });
});
