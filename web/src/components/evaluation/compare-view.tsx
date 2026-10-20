"use client";

// Compare view for two selected evaluation runs. The runs list and the
// comparison each hold explicit query states — failures render as errors
// with retry, never as empty data. Comparison results are bound to the
// ordered run pair that requested them: changing either selection remounts
// the comparison content (keyed by the pair), invalidating in-flight
// requests so stale responses, including A/B -> A/C -> A/B round trips,
// cannot overwrite the current view.

import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, GitCompare } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { evaluationApi, RunCompareResult, RunListItem } from "@/lib/api-client";

function RunSelector({
  value,
  onChange,
  runs,
  placeholder,
}: {
  value: string | null;
  onChange: (id: string) => void;
  runs: RunListItem[];
  placeholder: string;
}) {
  return (
    <Select value={value ?? undefined} onValueChange={onChange}>
      <SelectTrigger className="w-72">
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        {runs.map((run) => (
          <SelectItem key={run.id} value={run.id}>
            {run.id.slice(0, 8)} · {new Date(run.created_at).toLocaleString()}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

type RunsState =
  | { kind: "loading" }
  | { kind: "error"; code: string | null; message: string }
  | { kind: "ready"; data: RunListItem[] };

type CompareState =
  | { kind: "idle" }
  | { kind: "comparing" }
  | { kind: "error"; code: string | null; message: string }
  | { kind: "ready"; data: RunCompareResult };

/** Parse the error shapes this view can actually receive: the api-client
 * throws the response body verbatim, so errors arrive as the platform
 * envelope {detail:{error:{code,message}}}, as a plain string `detail`
 * (the comparison endpoint's 422s), or as the client's own {error:...}
 * fallback when the body cannot be parsed. */
export function parseCompareError(
  err: unknown, fallbackMessage = "Comparison failed",
): { code: string | null; message: string } {
  const body = err as {
    detail?: unknown;
    error?: { code?: string; message?: string };
  } | null;
  if (body && typeof body === "object") {
    const detailError =
      body.detail && typeof body.detail === "object"
        ? (body.detail as { error?: { code?: string; message?: string } }).error
        : undefined;
    if (detailError?.message) {
      return { code: detailError.code ?? null, message: detailError.message };
    }
    if (typeof body.detail === "string" && body.detail) {
      return { code: null, message: body.detail };
    }
    if (body.error?.message) {
      return { code: body.error.code ?? null, message: body.error.message };
    }
  }
  return { code: null, message: err instanceof Error ? err.message : fallbackMessage };
}

export function CompareView() {
  const [runsState, setRunsState] = useState<RunsState>({ kind: "loading" });
  const [baselineId, setBaselineId] = useState<string | null>(null);
  const [candidateId, setCandidateId] = useState<string | null>(null);
  const [submitSeq, setSubmitSeq] = useState(0);
  const [reloadTick, setReloadTick] = useState(0);

  useEffect(() => {
    let active = true;
    setRunsState({ kind: "loading" });
    evaluationApi
      .listRuns()
      .then((res) => {
        if (!active) return;
        setRunsState({ kind: "ready", data: res.items.filter((r) => r.status === "completed") });
      })
      .catch((err) => {
        if (active) {
          const parsed = parseCompareError(err, "Run list query failed");
          setRunsState({ kind: "error", code: parsed.code, message: parsed.message });
        }
      });
    return () => { active = false; };
  }, [reloadTick]);

  const runs = runsState.kind === "ready" ? runsState.data : [];
  const sameSelection = baselineId !== null && baselineId === candidateId;
  const pairKey = `${baselineId ?? ""}|${candidateId ?? ""}`;
  // A selection change starts a fresh, unsubmitted pair instance: without
  // resetting the counter the remounted content would auto-fire a compare
  // from the previous selection's submit tick.
  function changeBaseline(id: string) {
    if (id === baselineId) return;
    setBaselineId(id);
    setSubmitSeq(0);
  }

  function changeCandidate(id: string) {
    if (id === candidateId) return;
    setCandidateId(id);
    setSubmitSeq(0);
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Select Runs</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {runsState.kind === "loading" && <p data-testid="run-list-loading">Run 列表加载中…</p>}
          {runsState.kind === "error" && (
            <div data-testid="run-list-error" className="space-y-2">
              <p role="alert">
                Run 列表查询失败：{runsState.code && `[${runsState.code}] `}{runsState.message}
              </p>
              <Button variant="outline" size="sm" onClick={() => setReloadTick((t) => t + 1)}>
                重试
              </Button>
            </div>
          )}
          {runsState.kind === "ready" && (
            <>
              <div className="flex flex-wrap items-center gap-3">
                <RunSelector value={baselineId} onChange={changeBaseline} runs={runs} placeholder="Baseline run" />
                <GitCompare className="h-4 w-4 text-muted-foreground" />
                <RunSelector value={candidateId} onChange={changeCandidate} runs={runs} placeholder="Candidate run" />
                <Button
                  size="sm"
                  onClick={() => setSubmitSeq((t) => t + 1)}
                  disabled={!baselineId || !candidateId || sameSelection}
                >
                  Compare
                </Button>
              </div>
              {runs.length === 0 && (
                <p data-testid="run-list-empty" className="text-sm text-muted-foreground">
                  暂无已完成的评测 Run。
                </p>
              )}
              {sameSelection && (
                <p data-testid="same-run-prompt" className="text-sm text-destructive">
                  Baseline 与 Candidate 不能是同一个 Run。
                </p>
              )}
            </>
          )}
          <p className="text-xs text-muted-foreground">
            Comparisons assume both runs use the same evaluator set — differing evaluator prompts make
            deltas meaningless.
          </p>
        </CardContent>
      </Card>

      <CompareContent
        key={pairKey}
        baselineId={baselineId}
        candidateId={candidateId}
        submitSeq={submitSeq}
      />
    </div>
  );
}

function CompareContent({
  baselineId,
  candidateId,
  submitSeq,
}: {
  baselineId: string | null;
  candidateId: string | null;
  submitSeq: number;
}) {
  const [state, setState] = useState<CompareState>({ kind: "idle" });
  // Monotonic request token: any newer compare (submit or retry) invalidates
  // every in-flight one; remounting on pair change plus the cleanup token
  // drop stale responses from the previous pair, including A/B -> A/C -> A/B.
  const seq = useRef(0);

  const runCompare = useCallback(async () => {
    if (!baselineId || !candidateId || baselineId === candidateId) {
      return;
    }
    const requestSeq = ++seq.current;
    setState({ kind: "comparing" });
    try {
      const result = await evaluationApi.compareRuns(baselineId, candidateId);
      if (requestSeq !== seq.current) return;
      setState({ kind: "ready", data: result });
    } catch (err) {
      if (requestSeq !== seq.current) return;
      const parsed = parseCompareError(err);
      setState({ kind: "error", code: parsed.code, message: parsed.message });
    }
  }, [baselineId, candidateId]);

  useEffect(() => {
    if (submitSeq > 0) void runCompare();
    return () => { seq.current += 1; };
  }, [submitSeq, runCompare]);

  return (
    <>
      {state.kind === "idle" && (
        <Card>
          <CardContent>
            <p data-testid="compare-idle" className="py-6 text-center text-sm text-muted-foreground">
              选择 Baseline 与 Candidate 后点击 Compare。
            </p>
          </CardContent>
        </Card>
      )}
      {state.kind === "comparing" && (
        <Card>
          <CardContent>
            <p data-testid="compare-loading" className="py-6 text-center text-sm text-muted-foreground">
              比较进行中…
            </p>
          </CardContent>
        </Card>
      )}
      {state.kind === "error" && (
        <Card>
          <CardContent>
            <div data-testid="compare-error" className="space-y-2">
              <p role="alert">
                比较失败：{state.code && `[${state.code}] `}{state.message}
              </p>
              <Button variant="outline" size="sm" onClick={() => void runCompare()}>
                重试
              </Button>
            </div>
          </CardContent>
        </Card>
      )}
      {state.kind === "ready" && (
        <>
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle className="text-sm font-medium">Per-Metric Deltas</CardTitle>
              {state.data.overall_regressed && (
                <Badge variant="destructive">
                  <AlertTriangle className="h-3 w-3" /> regressed
                </Badge>
              )}
            </CardHeader>
            <CardContent>
              <div className="space-y-2" data-testid="compare-ready">
                {state.data.metrics.map((m) => (
                  <div key={m.metric} className="flex items-center justify-between rounded-md border p-3">
                    <span className="text-sm font-medium">{m.metric}</span>
                    <span className="flex items-center gap-3 text-sm">
                      <span className="text-muted-foreground">
                        {(m.baseline_avg * 100).toFixed(1)}% → {(m.candidate_avg * 100).toFixed(1)}%
                      </span>
                      <span className={m.delta < 0 ? "font-bold text-destructive" : "font-bold text-green-600"}>
                        {m.delta >= 0 ? "+" : ""}
                        {(m.delta * 100).toFixed(1)}pp
                      </span>
                      {m.is_regression && <Badge variant="destructive">regression</Badge>}
                    </span>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-medium">Paired Deltas &amp; Drift</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <div className="flex justify-between border-b pb-1">
                <span className="text-muted-foreground">Token usage delta</span>
                <span className="font-medium">{state.data.token_usage_delta}</span>
              </div>
              <div className="flex justify-between border-b pb-1">
                <span className="text-muted-foreground">Latency delta</span>
                <span className="font-medium">{state.data.latency_delta_ms} ms</span>
              </div>
              <div className="flex justify-between border-b pb-1">
                <span className="text-muted-foreground">Cost delta</span>
                <span className="font-medium">{state.data.cost_delta ?? "n/a"}</span>
              </div>
              <div className="flex items-center justify-between pt-1">
                <span className="text-muted-foreground">Dataset drift</span>
                {state.data.dataset_drift ? (
                  <Badge variant="destructive">
                    {state.data.dataset_drift.changed_item_ids.length} items changed
                  </Badge>
                ) : (
                  <Badge variant="outline">none</Badge>
                )}
              </div>
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">Node drift</span>
                {state.data.node_drift ? (
                  <Badge variant="secondary">{state.data.node_drift.length} sessions</Badge>
                ) : (
                  <Badge variant="outline">none</Badge>
                )}
              </div>
            </CardContent>
          </Card>
        </>
      )}
    </>
  );
}
