"use client";

import { useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { BarChart } from "@/components/ui/bar-chart";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { DistributionsReport, evaluationApi, RunListItem, RunScoreItem } from "@/lib/api-client";

const COLORS = ["hsl(var(--chart-1))"];

/** Source badges get distinct colors so judge / deterministic / human scores are distinguishable. */
const SOURCE_VARIANTS: Record<string, "default" | "secondary" | "outline"> = {
  llm_judge: "default",
  deterministic: "secondary",
  human: "outline",
};

interface RunReportViewProps {
  startDate: string;
  endDate: string;
}

export function RunReportView({ startDate, endDate }: RunReportViewProps) {
  const [runs, setRuns] = useState<RunListItem[]>([]);
  const [runId, setRunId] = useState<string | null>(null);
  const [distributions, setDistributions] = useState<DistributionsReport | null>(null);
  const [scores, setScores] = useState<RunScoreItem[]>([]);
  const [expandedScore, setExpandedScore] = useState<string | null>(null);

  useEffect(() => {
    evaluationApi
      .listRuns()
      .then((res) => {
        const completed = res.items.filter((r) => r.status === "completed");
        setRuns(completed);
        if (completed.length > 0) {
          setRunId(completed[0].id);
        }
      })
      .catch(() => setRuns([]));
  }, []);

  const loadRun = useCallback(async (id: string) => {
    try {
      setDistributions(await evaluationApi.getDistributions({ run_id: id }));
    } catch {
      setDistributions(null);
    }
    try {
      const scoreRes = await evaluationApi.listRunScores(id);
      setScores([...scoreRes.items].sort((a, b) => a.value - b.value));
    } catch {
      setScores([]);
    }
  }, []);

  useEffect(() => {
    if (runId) {
      loadRun(runId);
    }
  }, [runId, loadRun, startDate, endDate]);

  const lowScoreList = scores.slice(0, 20);

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Run</CardTitle>
        </CardHeader>
        <CardContent>
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
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Score Distributions</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {distributions && distributions.metrics.length > 0 ? (
            distributions.metrics.map((metric) => (
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
            ))
          ) : (
            <div className="flex h-[180px] items-center justify-center text-muted-foreground">
              {runId ? "No scores recorded for this run." : "Select a run to see distributions."}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Low Scores</CardTitle>
        </CardHeader>
        <CardContent>
          {lowScoreList.length > 0 ? (
            <div className="space-y-2">
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
                      <Badge variant={SOURCE_VARIANTS[score.source] ?? "outline"}>{score.source}</Badge>
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
            <div className="py-6 text-center text-sm text-muted-foreground">
              No scores recorded for this run.
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
