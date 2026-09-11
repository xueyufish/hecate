"use client";

import { useCallback, useEffect, useState } from "react";
import { ClipboardCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { OverviewView } from "@/components/evaluation/overview-view";
import { OnlineView } from "@/components/evaluation/online-view";
import { RunReportView } from "@/components/evaluation/run-report-view";
import { CompareView } from "@/components/evaluation/compare-view";
import { evaluationApi, OverviewReport } from "@/lib/api-client";

const TABS = ["overview", "online", "runs", "compare"] as const;
type Tab = (typeof TABS)[number];

const TAB_LABELS: Record<Tab, string> = {
  overview: "Overview",
  online: "Online Quality",
  runs: "Run Report",
  compare: "Compare",
};

export default function EvaluationPage() {
  const [tab, setTab] = useState<Tab>("overview");
  const [days, setDays] = useState<number>(30);
  const [overview, setOverview] = useState<OverviewReport | null>(null);
  const [loading, setLoading] = useState(true);

  const dateRange = useCallback(() => {
    const end = new Date();
    const start = new Date(end.getTime() - days * 86400000);
    return { start: start.toISOString(), end: end.toISOString() };
  }, [days]);

  const fetchOverview = useCallback(async () => {
    setLoading(true);
    const { start, end } = dateRange();
    try {
      setOverview(await evaluationApi.getOverview({ start_date: start, end_date: end }));
    } catch {
      setOverview(null);
    } finally {
      setLoading(false);
    }
  }, [dateRange]);

  useEffect(() => {
    fetchOverview();
  }, [fetchOverview]);

  const hasData =
    overview !== null &&
    (overview.volume.completed_runs > 0 || overview.volume.online_scored > 0);

  return (
    <div className="container mx-auto space-y-6 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Evaluation</h1>
          <p className="text-muted-foreground">Quality reports across offline runs and online scoring</p>
        </div>
        <div className="flex items-center gap-2">
          <Select value={String(days)} onValueChange={(v) => setDays(Number(v))}>
            <SelectTrigger className="w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="7">Last 7 days</SelectItem>
              <SelectItem value="30">Last 30 days</SelectItem>
              <SelectItem value="90">Last 90 days</SelectItem>
            </SelectContent>
          </Select>
          <Button variant="outline" size="sm" onClick={fetchOverview}>
            Refresh
          </Button>
        </div>
      </div>

      {!loading && !hasData ? (
        <Card>
          <CardContent className="py-12 text-center">
            <ClipboardCheck className="mx-auto mb-4 h-12 w-12 text-muted-foreground" />
            <h3 className="mb-2 text-lg font-medium">No evaluation data</h3>
            <p className="text-sm text-muted-foreground">
              Reports appear once evaluation runs complete or online tasks start scoring traces.
              Trigger a run from evaluation tasks to get started.
            </p>
          </CardContent>
        </Card>
      ) : (
        <>
          <div className="flex gap-1 border-b">
            {TABS.map((t) => (
              <button
                key={t}
                className={`rounded-t-md px-4 py-2 text-sm ${
                  tab === t
                    ? "border-b-2 border-primary font-medium text-primary"
                    : "text-muted-foreground hover:bg-muted"
                }`}
                onClick={() => setTab(t)}
              >
                {TAB_LABELS[t]}
              </button>
            ))}
          </div>

          {overview && (
            <>
              <div className={tab === "overview" ? "" : "hidden"}>
                <OverviewView overview={overview} startDate={dateRange().start} endDate={dateRange().end} />
              </div>
              <div className={tab === "online" ? "" : "hidden"}>
                <OnlineView startDate={dateRange().start} endDate={dateRange().end} />
              </div>
              <div className={tab === "runs" ? "" : "hidden"}>
                <RunReportView startDate={dateRange().start} endDate={dateRange().end} />
              </div>
              <div className={tab === "compare" ? "" : "hidden"}>
                <CompareView />
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
