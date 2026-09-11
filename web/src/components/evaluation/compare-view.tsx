"use client";

import { useCallback, useEffect, useState } from "react";
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

export function CompareView() {
  const [runs, setRuns] = useState<RunListItem[]>([]);
  const [baselineId, setBaselineId] = useState<string | null>(null);
  const [candidateId, setCandidateId] = useState<string | null>(null);
  const [result, setResult] = useState<RunCompareResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    evaluationApi
      .listRuns()
      .then((res) => setRuns(res.items.filter((r) => r.status === "completed")))
      .catch(() => setRuns([]));
  }, []);

  const compare = useCallback(async () => {
    if (!baselineId || !candidateId || baselineId === candidateId) {
      return;
    }
    setLoading(true);
    setError(null);
    try {
      setResult(await evaluationApi.compareRuns(baselineId, candidateId));
    } catch (err) {
      const message =
        typeof err === "object" && err !== null && "error" in err
          ? String((err as { error: { message: string } }).error.message)
          : "Comparison failed";
      setResult(null);
      setError(message);
    } finally {
      setLoading(false);
    }
  }, [baselineId, candidateId]);

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Select Runs</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-center gap-3">
            <RunSelector value={baselineId} onChange={setBaselineId} runs={runs} placeholder="Baseline run" />
            <GitCompare className="h-4 w-4 text-muted-foreground" />
            <RunSelector value={candidateId} onChange={setCandidateId} runs={runs} placeholder="Candidate run" />
            <Button size="sm" onClick={compare} disabled={!baselineId || !candidateId || loading}>
              {loading ? "Comparing…" : "Compare"}
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            Comparisons assume both runs use the same evaluator set — differing evaluator prompts make
            deltas meaningless.
          </p>
          {error && <div className="text-sm text-destructive">{error}</div>}
        </CardContent>
      </Card>

      {result && (
        <>
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle className="text-sm font-medium">Per-Metric Deltas</CardTitle>
              {result.overall_regressed && (
                <Badge variant="destructive">
                  <AlertTriangle className="h-3 w-3" /> regressed
                </Badge>
              )}
            </CardHeader>
            <CardContent>
              <div className="space-y-2">
                {result.metrics.map((m) => (
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
              <CardTitle className="text-sm font-medium">Paired Deltas & Drift</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <div className="flex justify-between border-b pb-1">
                <span className="text-muted-foreground">Token usage delta</span>
                <span className="font-medium">{result.token_usage_delta}</span>
              </div>
              <div className="flex justify-between border-b pb-1">
                <span className="text-muted-foreground">Latency delta</span>
                <span className="font-medium">{result.latency_delta_ms} ms</span>
              </div>
              <div className="flex justify-between border-b pb-1">
                <span className="text-muted-foreground">Cost delta</span>
                <span className="font-medium">{result.cost_delta ?? "n/a"}</span>
              </div>
              <div className="flex items-center justify-between pt-1">
                <span className="text-muted-foreground">Dataset drift</span>
                {result.dataset_drift ? (
                  <Badge variant="destructive">
                    {result.dataset_drift.changed_item_ids.length} items changed
                  </Badge>
                ) : (
                  <Badge variant="outline">none</Badge>
                )}
              </div>
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">Node drift</span>
                {result.node_drift ? (
                  <Badge variant="secondary">{result.node_drift.length} sessions</Badge>
                ) : (
                  <Badge variant="outline">none</Badge>
                )}
              </div>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
