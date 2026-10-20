// CompareView query-state hardening: explicit states for the runs list and
// the comparison, backend error consumption for both detail shapes, pair
// selection clearing, stale-response rejection across A/B -> A/C -> A/B,
// current-pair retries, and identical-run disabling.

import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CompareView, parseCompareError } from "./compare-view";
import type { RunCompareResult, RunListItem } from "@/lib/api-client";

const evaluationApiMock = vi.hoisted(() => ({
  compareRuns: vi.fn(),
  listRuns: vi.fn(),
}));

vi.mock("@/lib/api-client", () => ({
  api: { get: vi.fn(), post: vi.fn() },
  evaluationApi: evaluationApiMock,
}));

const RUN_A = "aaaaaaaa-1111-1111-1111-111111111111";
const RUN_B = "bbbbbbbb-2222-2222-2222-222222222222";
const RUN_C = "cccccccc-3333-3333-3333-333333333333";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function run(id: string): RunListItem {
  return {
    id, dataset_id: "dataset-1", status: "completed", summary: null,
    created_at: "2026-09-05T10:00:00Z", completed_at: null,
  };
}

function compareResult(metric: string): RunCompareResult {
  return {
    baseline_run_id: "baseline",
    candidate_run_id: "candidate",
    metrics: [{ metric, baseline_avg: 0.7, candidate_avg: 0.8, delta: 0.1, is_regression: false }],
    token_usage_delta: 10,
    latency_delta_ms: -20,
    cost_delta: null,
    dataset_drift: null,
    node_drift: null,
    overall_regressed: false,
  };
}

function rejectBody(detail: unknown) {
  return { detail };
}

/** Scope to the chosen selector and open it before selecting an option. */
async function selectRun(user: ReturnType<typeof userEvent.setup>, id: string, index: number) {
  const options = await screen.findAllByRole("option", { name: new RegExp(id.slice(0, 8)) });
  const root = options[index].parentElement?.closest<HTMLElement>("div.relative");
  if (!root) throw new Error("Run selector not found");
  await user.click(within(root).getByRole("button"));
  await user.click(within(root).getByRole("option", { name: new RegExp(id.slice(0, 8)) }));
}

async function selectBaseline(user: ReturnType<typeof userEvent.setup>, id: string) {
  await selectRun(user, id, 0);
}

async function selectCandidate(user: ReturnType<typeof userEvent.setup>, id: string) {
  await selectRun(user, id, 1);
}

async function clickCompare(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Compare" }));
}

beforeEach(() => {
  evaluationApiMock.listRuns.mockReset();
  evaluationApiMock.compareRuns.mockReset();
});

describe("CompareView query states", () => {
  it("renders comparison deltas for the selected pair", async () => {
    const user = userEvent.setup();
    evaluationApiMock.listRuns.mockResolvedValue({ items: [run(RUN_A), run(RUN_B)], total: 2 });
    evaluationApiMock.compareRuns.mockResolvedValue(compareResult("faithfulness"));
    render(<CompareView />);

    await screen.findByTestId("compare-idle");
    await selectBaseline(user, RUN_A);
    await selectCandidate(user, RUN_B);
    await clickCompare(user);

    expect(await screen.findByTestId("compare-ready")).toHaveTextContent("faithfulness");
    expect(evaluationApiMock.compareRuns).toHaveBeenCalledWith(RUN_A, RUN_B);
  });

  it("shows run list failure with code and message, then recovers on retry", async () => {
    evaluationApiMock.listRuns
      .mockRejectedValueOnce({ detail: { error: { code: "FORBIDDEN", message: "workspace required" } } })
      .mockResolvedValue({ items: [run(RUN_A), run(RUN_B)], total: 2 });
    render(<CompareView />);

    const error = await screen.findByTestId("run-list-error");
    expect(error).toHaveTextContent("FORBIDDEN");
    expect(error).toHaveTextContent("workspace required");
    expect(screen.queryByTestId("run-list-empty")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByTestId("compare-idle")).toBeInTheDocument();
    expect(screen.queryByTestId("run-list-error")).not.toBeInTheDocument();
    expect(evaluationApiMock.listRuns).toHaveBeenCalledTimes(2);
  });

  it("renders a completed-empty list as an empty state with compare disabled", async () => {
    evaluationApiMock.listRuns.mockResolvedValue({ items: [], total: 0 });
    render(<CompareView />);

    expect(await screen.findByTestId("run-list-empty")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Compare" })).toBeDisabled();
    expect(screen.queryByTestId("run-list-error")).not.toBeInTheDocument();
    expect(evaluationApiMock.compareRuns).not.toHaveBeenCalled();
  });

  it("consumes structured envelope errors with code and message", async () => {
    const user = userEvent.setup();
    evaluationApiMock.listRuns.mockResolvedValue({ items: [run(RUN_A), run(RUN_C)], total: 2 });
    evaluationApiMock.compareRuns.mockRejectedValue(
      rejectBody({ error: { code: "NOT_FOUND", message: "Run not found", details: null } }),
    );
    render(<CompareView />);
    await screen.findByTestId("compare-idle");
    await selectBaseline(user, RUN_A);
    await selectCandidate(user, RUN_C);
    await clickCompare(user);

    const error = await screen.findByTestId("compare-error");
    expect(error).toHaveTextContent("[NOT_FOUND]");
    expect(error).toHaveTextContent("Run not found");
    expect(screen.queryByText("Comparison failed")).not.toBeInTheDocument();
  });

  it("consumes the string detail of 422 responses", async () => {
    const user = userEvent.setup();
    evaluationApiMock.listRuns.mockResolvedValue({ items: [run(RUN_A), run(RUN_B)], total: 2 });
    evaluationApiMock.compareRuns.mockRejectedValue(
      rejectBody("runs reference different datasets; comparison not meaningful"),
    );
    render(<CompareView />);
    await screen.findByTestId("compare-idle");
    await selectBaseline(user, RUN_A);
    await selectCandidate(user, RUN_B);
    await clickCompare(user);

    const error = await screen.findByTestId("compare-error");
    expect(error).toHaveTextContent("runs reference different datasets; comparison not meaningful");
    expect(screen.queryByText("Comparison failed")).not.toBeInTheDocument();
  });

  it("disables Compare and prompts when both selections are the same run", async () => {
    const user = userEvent.setup();
    evaluationApiMock.listRuns.mockResolvedValue({ items: [run(RUN_A), run(RUN_B)], total: 2 });
    render(<CompareView />);
    await screen.findByTestId("compare-idle");

    await selectBaseline(user, RUN_A);
    await selectCandidate(user, RUN_A);
    expect(screen.getByTestId("same-run-prompt")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Compare" })).toBeDisabled();
    expect(evaluationApiMock.compareRuns).not.toHaveBeenCalled();
  });

  it("retries with the currently selected pair", async () => {
    const user = userEvent.setup();
    evaluationApiMock.listRuns.mockResolvedValue({ items: [run(RUN_A), run(RUN_C)], total: 2 });
    evaluationApiMock.compareRuns
      .mockRejectedValueOnce(rejectBody({ error: { code: "DB_DOWN", message: "reports unavailable" } }))
      .mockResolvedValue(compareResult("metric-ac"));
    render(<CompareView />);
    await screen.findByTestId("compare-idle");
    await selectBaseline(user, RUN_A);
    await selectCandidate(user, RUN_C);
    await clickCompare(user);
    expect(await screen.findByTestId("compare-error")).toHaveTextContent("DB_DOWN");

    await user.click(
      within(screen.getByTestId("compare-error")).getByRole("button", { name: "重试" }),
    );
    expect(evaluationApiMock.compareRuns).toHaveBeenLastCalledWith(RUN_A, RUN_C);
    expect(await screen.findByTestId("compare-ready")).toHaveTextContent("metric-ac");
  });

  it("hides the previous result while a new comparison for the same pair runs", async () => {
    const user = userEvent.setup();
    const nextCompare = deferred<RunCompareResult>();
    evaluationApiMock.listRuns.mockResolvedValue({ items: [run(RUN_A), run(RUN_B)], total: 2 });
    evaluationApiMock.compareRuns
      .mockResolvedValueOnce(compareResult("metric-first"))
      .mockReturnValueOnce(nextCompare.promise);
    render(<CompareView />);
    await screen.findByTestId("compare-idle");
    await selectBaseline(user, RUN_A);
    await selectCandidate(user, RUN_B);
    await clickCompare(user);
    expect(await screen.findByTestId("compare-ready")).toHaveTextContent("metric-first");

    await clickCompare(user);
    // The previous result is hidden while the new comparison is in flight.
    expect(screen.getByTestId("compare-loading")).toBeInTheDocument();
    expect(screen.queryByTestId("compare-ready")).not.toBeInTheDocument();

    await act(async () => {
      nextCompare.resolve(compareResult("metric-second"));
    });
    expect(await screen.findByTestId("compare-ready")).toHaveTextContent("metric-second");
  });
});

describe("CompareView pair switching", () => {
  it.each(["success", "error"] as const)("drops a late stale %s from the previous pair", async (outcome) => {
    const user = userEvent.setup();
    const staleCompare = deferred<RunCompareResult>();
    listThreeRuns();
    evaluationApiMock.compareRuns.mockImplementation((baseline: string, candidate: string) => {
      if (baseline === RUN_A && candidate === RUN_B &&
          evaluationApiMock.compareRuns.mock.calls.length === 1) {
        return staleCompare.promise;
      }
      return Promise.resolve(compareResult(`metric-${candidate.slice(0, 2)}`));
    });
    render(<CompareView />);
    await screen.findByTestId("compare-idle");
    await selectBaseline(user, RUN_A);
    await selectCandidate(user, RUN_B);
    await clickCompare(user);
    expect(screen.getByTestId("compare-loading")).toBeInTheDocument();

    // Changing the selection clears the old pair and invalidates its request.
    await selectCandidate(user, RUN_C);
    expect(await screen.findByTestId("compare-idle")).toBeInTheDocument();
    expect(screen.queryByTestId("compare-loading")).not.toBeInTheDocument();

    await act(async () => {
      if (outcome === "success") staleCompare.resolve(compareResult("metric-stale"));
      else staleCompare.reject(rejectBody({ error: { code: "LATE", message: "stale failure" } }));
    });
    expect(screen.getByTestId("compare-idle")).toBeInTheDocument();
    expect(screen.queryByText("metric-stale")).not.toBeInTheDocument();
    expect(screen.queryByTestId("compare-error")).not.toBeInTheDocument();
  });

  it.each(["success", "error"] as const)("drops the first A/B %s after A/B -> A/C -> A/B", async (outcome) => {
    const user = userEvent.setup();
    const staleFirst = deferred<RunCompareResult>();
    let compareCalls = 0;
    listThreeRuns();
    evaluationApiMock.compareRuns.mockImplementation((baseline: string, candidate: string) => {
      compareCalls += 1;
      if (baseline === RUN_A && candidate === RUN_B && compareCalls === 1) {
        return staleFirst.promise;
      }
      return Promise.resolve(compareResult(candidate === RUN_B ? "metric-ab" : "metric-ac"));
    });
    render(<CompareView />);
    await screen.findByTestId("compare-idle");
    await selectBaseline(user, RUN_A);
    await selectCandidate(user, RUN_B);
    await clickCompare(user);
    expect(screen.getByTestId("compare-loading")).toBeInTheDocument();

    await selectCandidate(user, RUN_C);
    await clickCompare(user);
    expect(await screen.findByText("metric-ac")).toBeInTheDocument();

    await selectCandidate(user, RUN_B);
    await clickCompare(user);
    expect(await screen.findByText("metric-ab")).toBeInTheDocument();
    expect(evaluationApiMock.compareRuns).toHaveBeenLastCalledWith(RUN_A, RUN_B);

    await act(async () => {
      if (outcome === "success") staleFirst.resolve(compareResult("metric-stale"));
      else staleFirst.reject(rejectBody({ error: { code: "LATE", message: "stale failure" } }));
    });
    expect(screen.getByText("metric-ab")).toBeInTheDocument();
    expect(screen.queryByText("metric-stale")).not.toBeInTheDocument();
  });
});

it("parses both detail shapes and the client fallback", () => {
  expect(parseCompareError({ detail: { error: { code: "NOT_FOUND", message: "Run not found" } } }))
    .toEqual({ code: "NOT_FOUND", message: "Run not found" });
  expect(parseCompareError({ detail: "runs reference different datasets" }))
    .toEqual({ code: null, message: "runs reference different datasets" });
  expect(parseCompareError({ error: { code: "UNKNOWN", message: "Bad response" } }))
    .toEqual({ code: "UNKNOWN", message: "Bad response" });
  expect(parseCompareError(new Error("network down")))
    .toEqual({ code: null, message: "network down" });
  expect(parseCompareError(undefined)).toEqual({ code: null, message: "Comparison failed" });
});

function listThreeRuns() {
  evaluationApiMock.listRuns.mockResolvedValue({
    items: [run(RUN_A), run(RUN_B), run(RUN_C)],
    total: 3,
  });
}

describe("CompareView repeated selection", () => {
  it.each([
    ["baseline", "success"], ["candidate", "success"],
    ["baseline", "error"], ["candidate", "error"],
  ] as const)("keeps the in-flight result when %s is unchanged (%s)", async (side, outcome) => {
    const user = userEvent.setup();
    const pending = deferred<RunCompareResult>();
    listThreeRuns();
    evaluationApiMock.compareRuns.mockReturnValueOnce(pending.promise);
    render(<CompareView />);
    await selectBaseline(user, RUN_A);
    await selectCandidate(user, RUN_B);
    await clickCompare(user);
    await screen.findByTestId("compare-loading");
    if (side === "baseline") await selectBaseline(user, RUN_A);
    else await selectCandidate(user, RUN_B);
    await act(async () => {
      if (outcome === "success") pending.resolve(compareResult("same-selection-result"));
      else pending.reject({ detail: { error: { code: "CURRENT", message: "current failure" } } });
    });
    if (outcome === "success") expect(screen.getByTestId("compare-ready")).toHaveTextContent("same-selection-result");
    else expect(screen.getByTestId("compare-error")).toHaveTextContent("CURRENT");
    expect(screen.queryByTestId("compare-loading")).not.toBeInTheDocument();
    expect(evaluationApiMock.compareRuns).toHaveBeenCalledTimes(1);
  });

  it("keeps an in-flight retry when the current candidate is reselected", async () => {
    const user = userEvent.setup();
    const retry = deferred<RunCompareResult>();
    listThreeRuns();
    evaluationApiMock.compareRuns.mockRejectedValueOnce(new Error("temporary failure"))
      .mockReturnValueOnce(retry.promise);
    render(<CompareView />);
    await selectBaseline(user, RUN_A);
    await selectCandidate(user, RUN_B);
    await clickCompare(user);
    const error = await screen.findByTestId("compare-error");
    await user.click(within(error).getByRole("button", { name: "重试" }));
    await selectCandidate(user, RUN_B);
    await act(async () => { retry.resolve(compareResult("retry-result")); });
    expect(screen.getByTestId("compare-ready")).toHaveTextContent("retry-result");
    expect(evaluationApiMock.compareRuns.mock.calls).toEqual([[RUN_A, RUN_B], [RUN_A, RUN_B]]);
  });
});

it.each([
  [{ detail: "Run list unavailable" }, "Run list unavailable"],
  [{ error: { code: "UNKNOWN", message: "Bad Gateway" } }, "UNKNOWN"],
])("preserves the Run list error information for %j", async (body, text) => {
  evaluationApiMock.listRuns.mockRejectedValueOnce(body);
  render(<CompareView />);
  expect(await screen.findByTestId("run-list-error")).toHaveTextContent(text);
  expect(evaluationApiMock.compareRuns).not.toHaveBeenCalled();
});

it.each([
  ["baseline", "ready"], ["candidate", "ready"],
  ["baseline", "error"], ["candidate", "error"],
] as const)("clears the old %s pair's %s state without auto-submitting", async (side, state) => {
  const user = userEvent.setup();
  listThreeRuns();
  if (state === "ready") evaluationApiMock.compareRuns.mockResolvedValueOnce(compareResult("previous-result"));
  else evaluationApiMock.compareRuns.mockRejectedValueOnce(new Error("previous failure"));
  render(<CompareView />);
  await selectBaseline(user, RUN_A);
  await selectCandidate(user, RUN_B);
  await clickCompare(user);
  await screen.findByTestId(`compare-${state}`);
  if (side === "baseline") await selectBaseline(user, RUN_C);
  else await selectCandidate(user, RUN_C);
  expect(screen.getByTestId("compare-idle")).toBeInTheDocument();
  expect(screen.queryByTestId(`compare-${state}`)).not.toBeInTheDocument();
  expect(evaluationApiMock.compareRuns).toHaveBeenCalledTimes(1);
});

it.each(["success", "error"] as const)("drops an older same-pair %s after a newer comparison", async (outcome) => {
  const user = userEvent.setup();
  const old = deferred<RunCompareResult>();
  listThreeRuns();
  evaluationApiMock.compareRuns.mockReturnValueOnce(old.promise)
    .mockResolvedValueOnce(compareResult("newer-result"));
  render(<CompareView />);
  await selectBaseline(user, RUN_A);
  await selectCandidate(user, RUN_B);
  await clickCompare(user);
  await screen.findByTestId("compare-loading");
  await clickCompare(user);
  await screen.findByTestId("compare-ready");
  await act(async () => {
    if (outcome === "success") old.resolve(compareResult("older-result"));
    else old.reject(new Error("older failure"));
  });
  expect(screen.getByTestId("compare-ready")).toHaveTextContent("newer-result");
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("treats reversed selections as a new ordered pair", async () => {
  const user = userEvent.setup();
  listThreeRuns();
  evaluationApiMock.compareRuns.mockResolvedValue(compareResult("result"));
  render(<CompareView />);
  await selectBaseline(user, RUN_A);
  await selectCandidate(user, RUN_B);
  await clickCompare(user);
  await screen.findByTestId("compare-ready");
  await selectBaseline(user, RUN_B);
  expect(screen.getByTestId("same-run-prompt")).toBeInTheDocument();
  await selectCandidate(user, RUN_A);
  expect(screen.getByTestId("compare-idle")).toBeInTheDocument();
  await clickCompare(user);
  await screen.findByTestId("compare-ready");
  expect(evaluationApiMock.compareRuns.mock.calls).toEqual([[RUN_A, RUN_B], [RUN_B, RUN_A]]);
});
