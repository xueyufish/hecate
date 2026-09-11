"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, CheckCircle, Database, TrendingUp } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { LineChart } from "@/components/ui/line-chart";
import { evaluationApi, OverviewReport, TrendSeries, TrendsReport } from "@/lib/api-client";

const COLORS = [
  "hsl(var(--chart-1))",
  "hsl(var(--chart-2))",
  "hsl(var(--chart-3))",
  "hsl(var(--chart-4))",
  "hsl(var(--chart-5))",
];

interface OverviewViewProps {
  overview: OverviewReport;
  startDate: string;
  endDate: string;
}

interface TrendPoint {
  bucket: string;
  value: number;
  count: number;
}

/** Merge every series into one per-bucket average line for the overview chart. */
function mergeSeriesPoints(series: TrendsReport["series"]): TrendPoint[] {
  const byBucket = new Map<string, { total: number; count: number }>();
  for (const entry of series) {
    for (const point of entry.points) {
      const acc = byBucket.get(point.bucket) ?? { total: 0, count: 0 };
      acc.total += point.value;
      acc.count += 1;
      byBucket.set(point.bucket, acc);
    }
  }
  return [...byBucket.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([bucket, acc]) => ({ bucket, value: acc.total / acc.count, count: acc.count }));
}

export function OverviewView({ overview, startDate, endDate }: OverviewViewProps) {
  const [trends, setTrends] = useState<TrendSeries[]>([]);

  const fetchTrends = useCallback(async () => {
    try {
      const report = await evaluationApi.getTrends({
        dimension: "dataset",
        bucket: "day",
        start_date: startDate,
        end_date: endDate,
      });
      setTrends(report.series);
    } catch {
      setTrends([]);
    }
  }, [startDate, endDate]);

  useEffect(() => {
    fetchTrends();
  }, [fetchTrends]);

  const trendPoints = mergeSeriesPoints(trends);
  const quality =
    overview.quality.offline_pass_rate !== null
      ? `${(overview.quality.offline_pass_rate * 100).toFixed(1)}%`
      : overview.quality.online_avg_score !== null
        ? `${(overview.quality.online_avg_score * 100).toFixed(1)}%`
        : "—";

  return (
    <div className="space-y-4">
      <div className="grid gap-4 md:grid-cols-4">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Quality</CardTitle>
            <CheckCircle className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{quality}</div>
            <p
              className="text-xs text-muted-foreground"
              title="Mean pass_rate of completed offline runs with a threshold summary; online average score as fallback"
            >
              offline pass rate over {overview.quality.offline_runs_counted} runs
              {overview.quality.online_avg_score !== null
                ? ` · online avg ${(overview.quality.online_avg_score * 100).toFixed(1)}%`
                : ""}
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Volume</CardTitle>
            <TrendingUp className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{overview.volume.completed_runs}</div>
            <p className="text-xs text-muted-foreground">
              completed runs · {overview.volume.online_scored} online scored
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Coverage</CardTitle>
            <Database className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {(overview.coverage.low_sample_run_ratio * 100).toFixed(0)}%
            </div>
            <p
              className="text-xs text-muted-foreground"
              title="Share of completed runs executed on datasets with fewer than 20 items; expand small datasets (e.g. AI synthesis) to make quality numbers trustworthy"
            >
              low-sample runs · {overview.coverage.active_datasets} datasets
              {overview.coverage.median_items !== null
                ? ` · median ${overview.coverage.median_items} items`
                : ""}
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Eval Errors</CardTitle>
            <AlertTriangle className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{(overview.error_rate.ratio * 100).toFixed(1)}%</div>
            <p className="text-xs text-muted-foreground">
              {overview.error_rate.error_count} of {overview.error_rate.total_scores} scores failed
            </p>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Quality Trend</CardTitle>
        </CardHeader>
        <CardContent>
          {trendPoints.length > 0 ? (
            <LineChart
              data={trendPoints.map((p) => ({
                date: p.bucket.slice(5),
                value: Number((p.value * 100).toFixed(1)),
              }))}
              lines={[{ dataKey: "value", color: COLORS[0], name: "Score (%)" }]}
              xAxisKey="date"
              height={250}
            />
          ) : (
            <div className="flex h-[250px] items-center justify-center text-muted-foreground">
              No trend data in this window
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
