// RunReportView query-state hardening: failures render as errors with retry
// (never as empty data), the two report sections fail independently, run
// switches remount the content so stale responses — including A->B->A round
// trips — are dropped, and retries target the currently selected run.

import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RunReportView } from "./run-report-view";
import type { DistributionsReport, RunListItem, RunScoreItem } from "@/lib/api-client";

const evaluationApiMock = vi.hoisted(() => ({
  getDistributions: vi.fn(),
  listRuns: vi.fn(),
  listRunScores: vi.fn(),
}));

vi.mock("@/lib/api-client", () => ({
  api: { get: vi.fn(), post: vi.fn() },
  evaluationApi: evaluationApiMock,
}));

const RUN_A = "aaaaaaaa-1111-1111-1111-111111111111";
const RUN_B = "bbbbbbbb-2222-2222-2222-222222222222";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function run(id: string, status = "completed"): RunListItem {
  return {
    id, dataset_id: "dataset-1", status, summary: null,
    created_at: "2026-09-05T10:00:00Z", completed_at: null,
  };
}

function distReport(metricName: string): DistributionsReport {
  return {
    scope: "run",
    scope_id: "scope",
    metrics: [{
      metric_name: metricName,
      bins: Array.from({ length: 10 }, (_, i) => ({ lower: i / 10, upper: (i + 1) / 10, count: i === 9 ? 1 : 0 })),
      count: 1, error_count: 0, mean: 0.9, min: 0.9, max: 0.9,
    }],
  };
}

function score(id: string, value: number, source = "llm_judge"): RunScoreItem {
  return {
    id, run_id: "scope", item_id: "cccccccc-3333-3333-3333-333333333333",
    metric_name: "faithfulness", value, reasoning: `why-${id}`, source,
    created_at: "2026-09-05T10:00:00Z",
  };
}

function rejectWith(code: string, message: string) {
  return { detail: { error: { code, message } } };
}

function renderReport(props: { startDate?: string; endDate?: string } = {}) {
  return render(
    <RunReportView
      startDate={props.startDate ?? "2026-09-01"}
      endDate={props.endDate ?? "2026-09-11"}
    />,
  );
}

async function selectRun(user: ReturnType<typeof userEvent.setup>, id: string) {
  await user.click(await screen.findByRole("button", { name: /^(aaaaaaaa|bbbbbbbb)/ }));
  await user.click(screen.getByRole("option", { name: new RegExp(id.slice(0, 8)) }));
}

beforeEach(() => {
  evaluationApiMock.listRuns.mockReset();
  evaluationApiMock.getDistributions.mockReset();
  evaluationApiMock.listRunScores.mockReset();
});

describe("RunReportView query states", () => {
  it("renders distributions and low scores for the selected run", async () => {
    evaluationApiMock.listRuns.mockResolvedValue({ items: [run(RUN_A)], total: 1 });
    evaluationApiMock.getDistributions.mockResolvedValue(distReport("metric-a"));
    evaluationApiMock.listRunScores.mockResolvedValue({ items: [score("s-1", 0.2)], total: 1 });
    renderReport();

    expect(await screen.findByTestId("distributions-ready")).toHaveTextContent("metric-a");
    expect(screen.getByTestId("scores-ready")).toHaveTextContent("20%");
    expect(evaluationApiMock.getDistributions).toHaveBeenCalledWith({ run_id: RUN_A });
    expect(screen.queryByTestId("run-list-error")).not.toBeInTheDocument();
  });

  it("shows run list failure with code and message, then recovers on retry", async () => {
    evaluationApiMock.listRuns
      .mockRejectedValueOnce(rejectWith("FORBIDDEN", "workspace required"))
      .mockResolvedValue({ items: [run(RUN_A)], total: 1 });
    evaluationApiMock.getDistributions.mockResolvedValue(distReport("metric-a"));
    evaluationApiMock.listRunScores.mockResolvedValue({ items: [], total: 0 });
    renderReport();

    const error = await screen.findByTestId("run-list-error");
    expect(error).toHaveTextContent("FORBIDDEN");
    expect(error).toHaveTextContent("workspace required");
    expect(screen.queryByTestId("run-list-empty")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByTestId("distributions-ready")).toBeInTheDocument();
    expect(screen.queryByTestId("run-list-error")).not.toBeInTheDocument();
    expect(evaluationApiMock.listRuns).toHaveBeenCalledTimes(2);
  });

  it("renders a completed-empty list as an empty state, not an error", async () => {
    evaluationApiMock.listRuns.mockResolvedValue({ items: [run(RUN_A, "running")], total: 1 });
    renderReport();

    expect(await screen.findByTestId("run-list-empty")).toBeInTheDocument();
    expect(screen.getByText("Select a run to see distributions.")).toBeInTheDocument();
    expect(screen.queryByTestId("run-list-error")).not.toBeInTheDocument();
    expect(evaluationApiMock.getDistributions).not.toHaveBeenCalled();
  });

  it("keeps the score list when distributions fail", async () => {
    evaluationApiMock.listRuns.mockResolvedValue({ items: [run(RUN_A)], total: 1 });
    evaluationApiMock.getDistributions.mockRejectedValue(rejectWith("DB_DOWN", "reports unavailable"));
    evaluationApiMock.listRunScores.mockResolvedValue({ items: [score("s-1", 0.2)], total: 1 });
    renderReport();

    const error = await screen.findByTestId("distributions-error");
    expect(error).toHaveTextContent("DB_DOWN");
    expect(error).toHaveTextContent("reports unavailable");
    expect(screen.getByTestId("scores-ready")).toHaveTextContent("20%");
    // Failure must not masquerade as empty data.
    expect(screen.queryByTestId("distributions-empty")).not.toBeInTheDocument();
    expect(screen.queryByText("No scores recorded for this run.")).not.toBeInTheDocument();
  });

  it("keeps the distributions chart when the score list fails", async () => {
    evaluationApiMock.listRuns.mockResolvedValue({ items: [run(RUN_A)], total: 1 });
    evaluationApiMock.getDistributions.mockResolvedValue(distReport("metric-a"));
    evaluationApiMock.listRunScores.mockRejectedValue(rejectWith("DB_DOWN", "scores unavailable"));
    renderReport();

    const error = await screen.findByTestId("scores-error");
    expect(error).toHaveTextContent("scores unavailable");
    expect(screen.getByTestId("distributions-ready")).toHaveTextContent("metric-a");
    expect(screen.queryByTestId("scores-empty")).not.toBeInTheDocument();
  });
});

describe("RunReportView run switching", () => {
  function listTwoRuns() {
    evaluationApiMock.listRuns.mockResolvedValue({ items: [run(RUN_A), run(RUN_B)], total: 2 });
  }

  async function switchToRunB(user: ReturnType<typeof userEvent.setup>) {
    await selectRun(user, RUN_B);
  }

  it("hides previous run content immediately on run switch", async () => {
    const user = userEvent.setup();
    const runBDist = deferred<DistributionsReport>();
    const runBScores = deferred<{ items: RunScoreItem[]; total: number }>();
    listTwoRuns();
    evaluationApiMock.getDistributions.mockImplementation(({ run_id }: { run_id: string }) =>
      run_id === RUN_A ? Promise.resolve(distReport("metric-a")) : runBDist.promise);
    evaluationApiMock.listRunScores.mockImplementation((runId: string) =>
      runId === RUN_A
        ? Promise.resolve({ items: [score("s-1", 0.2)], total: 1 })
        : runBScores.promise);
    renderReport();
    await screen.findByText("metric-a");
    await user.click(await screen.findByText("20%"));
    expect(screen.getByText("why-s-1")).toBeInTheDocument();

    // Run B responses are held back, so the loading states the switch
    // produced are deterministic; the expanded row from Run A is gone.
    await switchToRunB(user);
    expect(screen.getByTestId("distributions-loading")).toBeInTheDocument();
    expect(screen.getByTestId("scores-loading")).toBeInTheDocument();
    expect(screen.queryByText("metric-a")).not.toBeInTheDocument();
    expect(screen.queryByText("why-s-1")).not.toBeInTheDocument();

    await act(async () => {
      runBDist.resolve(distReport("metric-b"));
      runBScores.resolve({ items: [score("s-b", 0.3)], total: 1 });
    });
    expect(await screen.findByText("metric-b")).toBeInTheDocument();
    expect(screen.getByTestId("scores-ready")).toHaveTextContent("30%");
  });

  it("drops a late stale success and error from the previous run", async () => {
    const user = userEvent.setup();
    const staleDist = deferred<DistributionsReport>();
    const staleScores = deferred<{ items: RunScoreItem[]; total: number }>();
    listTwoRuns();
    evaluationApiMock.getDistributions.mockImplementation(({ run_id }: { run_id: string }) =>
      run_id === RUN_A && evaluationApiMock.getDistributions.mock.calls.length === 1
        ? staleDist.promise
        : Promise.resolve(distReport(run_id === RUN_A ? "metric-a" : "metric-b")));
    let scoreCalls = 0;
    evaluationApiMock.listRunScores.mockImplementation((runId: string) => {
      scoreCalls += 1;
      if (runId === RUN_A && scoreCalls === 1) return staleScores.promise;
      return Promise.resolve({ items: [score("s-b", 0.3)], total: 1 });
    });
    renderReport();
    await screen.findByTestId("scores-loading");

    await switchToRunB(user);
    expect(await screen.findByText("metric-b")).toBeInTheDocument();

    await act(async () => {
      staleDist.resolve(distReport("metric-stale"));
      staleScores.reject(rejectWith("LATE", "stale scores failure"));
    });
    expect(screen.getByText("metric-b")).toBeInTheDocument();
    expect(screen.queryByText("metric-stale")).not.toBeInTheDocument();
    expect(screen.queryByTestId("scores-error")).not.toBeInTheDocument();
  });

  it.each(["success", "error"] as const)("drops a stale first-A %s after A -> B -> A", async (outcome) => {
    const user = userEvent.setup();
    const staleFirstA = deferred<DistributionsReport>();
    let distCalls = 0;
    listTwoRuns();
    evaluationApiMock.getDistributions.mockImplementation(({ run_id }: { run_id: string }) => {
      distCalls += 1;
      if (run_id === RUN_A && distCalls === 1) return staleFirstA.promise;
      return Promise.resolve(distReport(run_id === RUN_A ? "metric-a2" : "metric-b"));
    });
    evaluationApiMock.listRunScores.mockResolvedValue({ items: [], total: 0 });
    renderReport();
    await screen.findByTestId("distributions-loading");

    await switchToRunB(user);
    expect(await screen.findByText("metric-b")).toBeInTheDocument();
    await selectRun(user, RUN_A);
    expect(await screen.findByText("metric-a2")).toBeInTheDocument();
    expect(evaluationApiMock.getDistributions).toHaveBeenLastCalledWith({ run_id: RUN_A });

    await act(async () => {
      if (outcome === "success") staleFirstA.resolve(distReport("metric-stale"));
      else staleFirstA.reject(rejectWith("LATE", "stale failure"));
    });
    expect(screen.getByText("metric-a2")).toBeInTheDocument();
    expect(screen.queryByText("metric-stale")).not.toBeInTheDocument();
    expect(screen.queryByTestId("distributions-error")).not.toBeInTheDocument();
  });

  it("retries target the currently selected run", async () => {
    const user = userEvent.setup();
    listTwoRuns();
    evaluationApiMock.getDistributions.mockImplementation(({ run_id }: { run_id: string }) =>
      run_id === RUN_B
        ? Promise.reject(rejectWith("DB_DOWN", "reports unavailable"))
        : Promise.resolve(distReport("metric-a")));
    evaluationApiMock.listRunScores.mockResolvedValue({ items: [], total: 0 });
    renderReport();
    await screen.findByText("metric-a");

    await switchToRunB(user);
    expect(await screen.findByTestId("distributions-error")).toHaveTextContent("DB_DOWN");

    evaluationApiMock.getDistributions.mockImplementation(({ run_id }: { run_id: string }) =>
      Promise.resolve(distReport(run_id === RUN_A ? "metric-a" : "metric-b")));
    await user.click(
      within(screen.getByTestId("distributions-error")).getByRole("button", { name: "重试" }),
    );
    expect(evaluationApiMock.getDistributions).toHaveBeenLastCalledWith({ run_id: RUN_B });
    expect(await screen.findByText("metric-b")).toBeInTheDocument();
  });

  it("re-queries the current run when the date range changes", async () => {
    evaluationApiMock.listRuns.mockResolvedValue({ items: [run(RUN_A)], total: 1 });
    evaluationApiMock.getDistributions.mockResolvedValue(distReport("metric-a"));
    evaluationApiMock.listRunScores.mockResolvedValue({ items: [], total: 0 });
    const view = renderReport({ endDate: "2026-09-11" });
    await screen.findByTestId("distributions-ready");

    view.rerender(
      <RunReportView startDate="2026-09-01" endDate="2026-09-30" />,
    );
    expect(await screen.findByTestId("distributions-ready")).toBeInTheDocument();
    const calls = evaluationApiMock.getDistributions.mock.calls.map((call) => call[0]);
    expect(calls).toEqual([
      { run_id: RUN_A },
      { run_id: RUN_A },
    ]);
  });
});

it("renders prototype-colliding source values as raw text", async () => {
  evaluationApiMock.listRuns.mockResolvedValue({ items: [run(RUN_A)], total: 1 });
  evaluationApiMock.getDistributions.mockResolvedValue(distReport("metric-a"));
  evaluationApiMock.listRunScores.mockResolvedValue({
    items: [score("s-1", 0.2, "constructor"), score("s-2", 0.3, "__proto__")],
    total: 2,
  });
  renderReport();

  expect(await screen.findByTestId("scores-ready")).toBeInTheDocument();
  expect(screen.getByText("constructor")).toBeInTheDocument();
  expect(screen.getByText("__proto__")).toBeInTheDocument();
});
describe("RunReportView unselected state", () => {
  it("does not claim scores are empty while the Run list is still loading", () => {
    evaluationApiMock.listRuns.mockReturnValue(deferred<{ items: RunListItem[]; total: number }>().promise);
    renderReport();
    expect(screen.getByTestId("run-list-loading")).toBeInTheDocument();
    expect(screen.queryByText("No scores recorded for this run.")).not.toBeInTheDocument();
    expect(screen.getByText("Select a run to see scores.")).toBeInTheDocument();
    expect(evaluationApiMock.listRunScores).not.toHaveBeenCalled();
  });

  it.each(["FORBIDDEN", "NOT_FOUND", "INTERNAL_ERROR"])(
    "does not claim scores are empty after a %s list failure",
    async (code) => {
      evaluationApiMock.listRuns.mockRejectedValue(rejectWith(code, "Run list unavailable"));
      renderReport();
      expect(await screen.findByTestId("run-list-error")).toHaveTextContent(code);
      expect(screen.queryByText("No scores recorded for this run.")).not.toBeInTheDocument();
      expect(screen.getByText("Select a run to see scores.")).toBeInTheDocument();
      expect(evaluationApiMock.listRunScores).not.toHaveBeenCalled();
    },
  );

  it("keeps an unselected prompt when no completed Run exists", async () => {
    evaluationApiMock.listRuns.mockResolvedValue({ items: [], total: 0 });
    renderReport();
    await screen.findByTestId("run-list-empty");
    expect(screen.queryByText("No scores recorded for this run.")).not.toBeInTheDocument();
    expect(screen.getByText("Select a run to see scores.")).toBeInTheDocument();
  });

  it("shows empty scores only after successful queries for a selected Run", async () => {
    evaluationApiMock.listRuns.mockResolvedValue({ items: [run(RUN_A)], total: 1 });
    evaluationApiMock.getDistributions.mockResolvedValue({ scope: "run", scope_id: RUN_A, metrics: [] });
    evaluationApiMock.listRunScores.mockResolvedValue({ items: [], total: 0 });
    renderReport();
    expect(await screen.findByTestId("scores-empty")).toBeInTheDocument();
    expect(screen.getByTestId("distributions-empty")).toBeInTheDocument();
    expect(screen.getAllByText("No scores recorded for this run.")).toHaveLength(2);
  });
});

it.each(["distributions", "scores"] as const)(
  "retrying %s preserves the other section's in-flight request",
  async (side) => {
    const pendingDist = deferred<DistributionsReport>();
    const pendingScores = deferred<{ items: RunScoreItem[]; total: number }>();
    evaluationApiMock.listRuns.mockResolvedValue({ items: [run(RUN_A)], total: 1 });
    if (side === "distributions") {
      evaluationApiMock.getDistributions.mockRejectedValueOnce(rejectWith("TEMPORARY", "retry me"))
        .mockResolvedValueOnce(distReport("retried-dist"));
      evaluationApiMock.listRunScores.mockReturnValueOnce(pendingScores.promise);
    } else {
      evaluationApiMock.listRunScores.mockRejectedValueOnce(rejectWith("TEMPORARY", "retry me"))
        .mockResolvedValueOnce({ items: [score("retried", 0.4)], total: 1 });
      evaluationApiMock.getDistributions.mockReturnValueOnce(pendingDist.promise);
    }
    renderReport();
    const error = await screen.findByTestId(`${side}-error`);
    await userEvent.click(within(error).getByRole("button", { name: "重试" }));
    await screen.findByTestId(`${side}-ready`);
    await act(async () => {
      if (side === "distributions") pendingScores.resolve({ items: [score("pending", 0.5)], total: 1 });
      else pendingDist.resolve(distReport("pending-dist"));
    });
    expect(screen.getByTestId("scores-ready")).toBeInTheDocument();
    expect(screen.getByTestId("distributions-ready")).toBeInTheDocument();
    expect(evaluationApiMock.getDistributions).toHaveBeenCalledTimes(side === "distributions" ? 2 : 1);
    expect(evaluationApiMock.listRunScores).toHaveBeenCalledTimes(side === "scores" ? 2 : 1);
  },
);

it.each(["success", "error"] as const)(
  "discards stale same-Run %s after a date-triggered reload",
  async (outcome) => {
    const oldDist = deferred<DistributionsReport>();
    const oldScores = deferred<{ items: RunScoreItem[]; total: number }>();
    evaluationApiMock.listRuns.mockResolvedValue({ items: [run(RUN_A)], total: 1 });
    evaluationApiMock.getDistributions.mockReturnValueOnce(oldDist.promise)
      .mockResolvedValueOnce(distReport("current-dist"));
    evaluationApiMock.listRunScores.mockReturnValueOnce(oldScores.promise)
      .mockResolvedValueOnce({ items: [score("current", 0.6)], total: 1 });
    const view = renderReport();
    await screen.findByTestId("scores-loading");
    view.rerender(<RunReportView startDate="2026-09-01" endDate="2026-09-30" />);
    await screen.findByTestId("scores-ready");
    await act(async () => {
      if (outcome === "success") {
        oldDist.resolve(distReport("old-dist"));
        oldScores.resolve({ items: [score("old", 0.1)], total: 1 });
      } else {
        oldDist.reject(rejectWith("OLD", "old distribution failure"));
        oldScores.reject(rejectWith("OLD", "old score failure"));
      }
    });
    expect(screen.getByTestId("distributions-ready")).toHaveTextContent("current-dist");
    expect(screen.getByTestId("scores-ready")).toHaveTextContent("60%");
    expect(screen.queryByText("old-dist")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(evaluationApiMock.listRuns).toHaveBeenCalledTimes(1);
    expect(evaluationApiMock.getDistributions.mock.calls.map(([params]) => params))
      .toEqual([{ run_id: RUN_A }, { run_id: RUN_A }]);
    expect(evaluationApiMock.listRunScores.mock.calls.map(([id]) => id)).toEqual([RUN_A, RUN_A]);
  },
);

it("retries scores for the currently selected Run without reloading distributions", async () => {
  const user = userEvent.setup();
  evaluationApiMock.listRuns.mockResolvedValue({ items: [run(RUN_A), run(RUN_B)], total: 2 });
  evaluationApiMock.getDistributions.mockImplementation(({ run_id }: { run_id: string }) =>
    Promise.resolve(distReport(run_id === RUN_A ? "metric-a" : "metric-b")));
  let failedB = false;
  evaluationApiMock.listRunScores.mockImplementation((id: string) => {
    if (id === RUN_B && !failedB) {
      failedB = true;
      return Promise.reject(rejectWith("TEMPORARY", "retry B scores"));
    }
    return Promise.resolve({ items: [score(id, id === RUN_A ? 0.2 : 0.7)], total: 1 });
  });
  renderReport();
  await screen.findByTestId("scores-ready");
  await selectRun(user, RUN_B);
  const error = await screen.findByTestId("scores-error");
  await user.click(within(error).getByRole("button", { name: "重试" }));
  expect(await screen.findByTestId("scores-ready")).toHaveTextContent("70%");
  expect(evaluationApiMock.listRunScores).toHaveBeenLastCalledWith(RUN_B);
  expect(evaluationApiMock.getDistributions).toHaveBeenCalledTimes(2);
  expect(screen.getByTestId("distributions-ready")).toHaveTextContent("metric-b");
});
