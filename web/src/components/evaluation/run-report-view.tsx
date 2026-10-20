"use client";

// Run report view for one selected evaluation run. Three independent query
// states (runs list, distributions, low scores): failures render as errors
// with retry — never as empty data. Switching runs remounts the report
// content (keyed by run id) so stale responses, including A->B->A round
// trips, cannot overwrite the current selection.

import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { BarChart } from "@/components/ui/bar-chart";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { DistributionsReport, evaluationApi, RunListItem, RunScoreItem } from "@/lib/api-client";
import { taskErrorCode, taskErrorMessage } from "@/lib/task-types";

const COLORS = ["hsl(var(--chart-1))"];

/** Source badges get distinct colors so judge / deterministic / human scores are distinguishable. */
const SOURCE_VARIANTS: Record<string, "default" | "secondary" | "outline"> = {
  llm_judge: "default",
  deterministic: "secondary",
  human: "outline",
};

/** Own-key lookup: a `constructor` source must not resolve to a prototype member. */
function sourceVariant(source: string): "default" | "secondary" | "outline" {
  return Object.hasOwn(SOURCE_VARIANTS, source) ? SOURCE_VARIANTS[source] : "outline";
}

interface RunReportViewProps {
  startDate: string;
  endDate: string;
}

type QueryState<T> =
  | { kind: "loading" }
  | { kind: "error"; code: string | null; message: string }
  | { kind: "ready"; data: T };

function QueryError({
  testId, label, code, message, onRetry,
}: {
  testId: string;
  label: string;
  code: string | null;
  message: string;
  onRetry: () => void;
}) {
  return (
    <div data-testid={testId} className="space-y-2">
      <p role="alert">{label}查询失败：{code && `[${code}] `}{message}</p>
      <Button variant="outline" size="sm" onClick={onRetry}>
        重试
      </Button>
    </div>
  );
}

export function RunReportView({ startDate, endDate }: RunReportViewProps) {
  const [runsState, setRunsState] = useState<QueryState<RunListItem[]>>({ kind: "loading" });
  const [runId, setRunId] = useState<string | null>(null);
  const [reloadTick, setReloadTick] = useState(0);

  useEffect(() => {
    let active = true;
    setRunsState({ kind: "loading" });
    evaluationApi
      .listRuns()
      .then((res) => {
        if (!active) return;
        const completed = res.items.filter((r) => r.status === "completed");
        setRunsState({ kind: "ready", data: completed });
        // Keep the user's selection when a retry still returns it.
        setRunId((current) =>
          current && completed.some((r) => r.id === current)
            ? current
            : (completed[0]?.id ?? null),
        );
      })
      .catch((err) => {
        if (active) {
          setRunsState({ kind: "error", code: taskErrorCode(err), message: taskErrorMessage(err) });
        }
      });
    return () => { active = false; };
  }, [reloadTick]);

  const runs = runsState.kind === "ready" ? runsState.data : [];

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Run</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {runsState.kind === "loading" && <p data-testid="run-list-loading">Run 列表加载中…</p>}
          {runsState.kind === "error" && (
            <QueryError
              testId="run-list-error"
              label="Run 列表"
              code={runsState.code}
              message={runsState.message}
              onRetry={() => setReloadTick((t) => t + 1)}
            />
          )}
          {runsState.kind === "ready" && (
            <>
              <Select value={runId ?? undefined} onValueChange={setRunId}>
                <SelectTrigger className="w-96">
                  <SelectValue placeholder="Select a completed run" />
                </SelectTrigger>
                <SelectContent>
                  {runs.map((run) => (
                    <SelectItem key={run.id} value={run.id}>
                      {run.id.slice(0, 8)} · {new Date(run.created_at).toLocaleString()}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {runs.length === 0 && (
                <p data-testid="run-list-empty" className="text-sm text-muted-foreground">
                  暂无已完成的评测 Run。
                </p>
              )}
            </>
          )}
        </CardContent>
      </Card>

      {runId ? (
        <RunReportContent key={runId} runId={runId} startDate={startDate} endDate={endDate} />
      ) : (
        <>
          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-medium">Score Distributions</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex h-[180px] items-center justify-center text-muted-foreground">
                Select a run to see distributions.
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-medium">Low Scores</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="py-6 text-center text-sm text-muted-foreground">
                Select a run to see scores.
              </div>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}

function RunReportContent({
  runId, startDate, endDate,
}: {
  runId: string;
  startDate: string;
  endDate: string;
}) {
  const [distributions, setDistributions] = useState<QueryState<DistributionsReport>>({ kind: "loading" });
  const [scores, setScores] = useState<QueryState<RunScoreItem[]>>({ kind: "loading" });
  const [expandedScore, setExpandedScore] = useState<string | null>(null);
  // Per-side request tokens: retrying one side must not invalidate an
  // in-flight response on the other. Run switches remount this component;
  // date-range changes bump both tokens as a current-run refresh.
  const distSeq = useRef(0);
  const scoreSeq = useRef(0);

  const fetchDistributions = useCallback(async (seq: number) => {
    setDistributions({ kind: "loading" });
    try {
      const report = await evaluationApi.getDistributions({ run_id: runId });
      if (seq !== distSeq.current) return;
      setDistributions({ kind: "ready", data: report });
    } catch (err) {
      if (seq !== distSeq.current) return;
      setDistributions({ kind: "error", code: taskErrorCode(err), message: taskErrorMessage(err) });
    }
  }, [runId]);

  const fetchScores = useCallback(async (seq: number) => {
    setScores({ kind: "loading" });
    try {
      const scoreRes = await evaluationApi.listRunScores(runId);
      if (seq !== scoreSeq.current) return;
      setScores({ kind: "ready", data: [...scoreRes.items].sort((a, b) => a.value - b.value) });
    } catch (err) {
      if (seq !== scoreSeq.current) return;
      setScores({ kind: "error", code: taskErrorCode(err), message: taskErrorMessage(err) });
    }
  }, [runId]);

  useEffect(() => {
    distSeq.current += 1;
    scoreSeq.current += 1;
    void fetchDistributions(distSeq.current);
    void fetchScores(scoreSeq.current);
    return () => {
      distSeq.current += 1;
      scoreSeq.current += 1;
    };
  }, [runId, startDate, endDate, fetchDistributions, fetchScores]);

  function retryDistributions() {
    distSeq.current += 1;
    void fetchDistributions(distSeq.current);
  }

  function retryScores() {
    scoreSeq.current += 1;
    void fetchScores(scoreSeq.current);
  }

  const lowScoreList = scores.kind === "ready" ? scores.data.slice(0, 20) : [];

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Score Distributions</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {distributions.kind === "loading" && <p data-testid="distributions-loading">评分分布加载中…</p>}
          {distributions.kind === "error" && (
            <QueryError
              testId="distributions-error"
              label="评分分布"
              code={distributions.code}
              message={distributions.message}
              onRetry={retryDistributions}
            />
          )}
          {distributions.kind === "ready" && (distributions.data.metrics.length > 0 ? (
            <div data-testid="distributions-ready" className="space-y-4">
              {distributions.data.metrics.map((metric) => (
                <div key={metric.metric_name} className="space-y-1">
                  <div className="text-sm font-medium">
                    {metric.metric_name}
                    {metric.error_count > 0 && (
                      <Badge variant="destructive" className="ml-2">
                        {metric.error_count} errors
                      </Badge>
                    )}
                    {metric.mean !== null && (
                      <span className="ml-2 text-xs text-muted-foreground">mean {(metric.mean * 100).toFixed(1)}%</span>
                    )}
                  </div>
                  <BarChart
                    data={metric.bins.map((bin) => ({
                      name: `${(bin.lower * 100).toFixed(0)}-${(bin.upper * 100).toFixed(0)}`,
                      count: bin.count,
                    }))}
                    bars={[{ dataKey: "count", color: COLORS[0], name: "scores" }]}
                    xAxisKey="name"
                    height={180}
                  />
                </div>
              ))}
            </div>
          ) : (
            <div data-testid="distributions-empty" className="flex h-[180px] items-center justify-center text-muted-foreground">
              No scores recorded for this run.
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Low Scores</CardTitle>
        </CardHeader>
        <CardContent>
          {scores.kind === "loading" && <p data-testid="scores-loading">评分加载中…</p>}
          {scores.kind === "error" && (
            <QueryError
              testId="scores-error"
              label="评分"
              code={scores.code}
              message={scores.message}
              onRetry={retryScores}
            />
          )}
          {scores.kind === "ready" && (lowScoreList.length > 0 ? (
            <div data-testid="scores-ready" className="space-y-2">
              {lowScoreList.map((score) => (
                <div key={score.id} className="rounded-md border">
                  <button
                    className="flex w-full items-center justify-between p-3 text-left"
                    onClick={() => setExpandedScore(expandedScore === score.id ? null : score.id)}
                  >
                    <span className="flex items-center gap-2">
                      {expandedScore === score.id ? (
                        <ChevronDown className="h-4 w-4" />
                      ) : (
                        <ChevronRight className="h-4 w-4" />
                      )}
                      <span className="text-sm font-medium">{score.metric_name}</span>
                      <Badge variant={sourceVariant(score.source)}>{score.source}</Badge>
                    </span>
                    <span className="text-sm font-bold">
                      {score.value < 0 ? "error" : `${(score.value * 100).toFixed(0)}%`}
                    </span>
                  </button>
                  {expandedScore === score.id && (
                    <div className="border-t px-3 py-2 text-xs text-muted-foreground">
                      <div className="whitespace-pre-wrap">{score.reasoning ?? "No reasoning recorded."}</div>
                      <div className="mt-1 font-mono">item {score.item_id.slice(0, 8)}</div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <div data-testid="scores-empty" className="py-6 text-center text-sm text-muted-foreground">
              No scores recorded for this run.
            </div>
          ))}
        </CardContent>
      </Card>
    </>
  );
}
