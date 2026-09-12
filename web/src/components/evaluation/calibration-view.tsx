"use client";

import { useCallback, useEffect, useState } from "react";
import { Scale } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { CalibrationMetric, evaluationApi } from "@/lib/api-client";

const HEATMAP_BINS = 10;

function heatmapColor(count: number, max: number): string {
  if (count === 0) return "transparent";
  const alpha = 0.15 + 0.85 * (count / max);
  return `hsl(var(--chart-1) / ${alpha.toFixed(2)})`;
}

function Heatmap({ metric }: { metric: CalibrationMetric }) {
  const cells = new Map(metric.heatmap.map((c) => [`${c.machine_bin}:${c.human_bin}`, c.count]));
  const max = Math.max(1, ...metric.heatmap.map((c) => c.count));
  return (
    <div>
      <div
        className="grid gap-px"
        style={{ gridTemplateColumns: `1.5rem repeat(${HEATMAP_BINS}, minmax(0, 1fr))` }}
      >
        <div />
        {Array.from({ length: HEATMAP_BINS }, (_, x) => (
          <div key={`h${x}`} className="text-center text-[10px] text-muted-foreground">
            {(x / HEATMAP_BINS).toFixed(1)}
          </div>
        ))}
        {Array.from({ length: HEATMAP_BINS }, (_, y) => (
          <div key={`r${y}`} className="contents">
            <div className="text-right text-[10px] text-muted-foreground">{(y / HEATMAP_BINS).toFixed(1)}</div>
            {Array.from({ length: HEATMAP_BINS }, (_, x) => {
              const count = cells.get(`${x}:${y}`) ?? 0;
              return (
                <div
                  key={`${x}:${y}`}
                  className="aspect-square rounded-sm border border-border/50"
                  style={{ backgroundColor: heatmapColor(count, max) }}
                  title={`machine bin ${x} × human bin ${y}: ${count}`}
                />
              );
            })}
          </div>
        ))}
      </div>
      <div className="mt-1 text-[10px] text-muted-foreground">
        machine value (columns) × human value (rows), {HEATMAP_BINS} bins over [0, 1]
      </div>
    </div>
  );
}

function MetricCard({ metric }: { metric: CalibrationMetric }) {
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle className="text-sm font-medium">{metric.metric_name}</CardTitle>
        <Badge variant="outline">{metric.data_mode}</Badge>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
          <div>
            <div className="text-xs text-muted-foreground">paired samples</div>
            <div className="font-medium">{metric.pair_count}</div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground">agreement</div>
            <div className="font-medium">
              {metric.agreement_rate === null ? "—" : `${(metric.agreement_rate * 100).toFixed(1)}%`}
            </div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground">{metric.data_mode === "numeric" ? "MAE" : "Cohen's Kappa"}</div>
            <div className="font-medium">
              {metric.data_mode === "numeric"
                ? metric.mae === null
                  ? "—"
                  : metric.mae.toFixed(4)
                : metric.kappa === null
                  ? "—"
                  : metric.kappa.toFixed(4)}
            </div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground">unpaired</div>
            <div className="font-medium">
              {metric.machine_only_count} / {metric.human_only_count}
            </div>
          </div>
        </div>
        {metric.data_mode === "numeric" && metric.heatmap.length > 0 && <Heatmap metric={metric} />}
        {metric.data_mode === "categorical" && metric.pair_count === 0 && (
          <div className="text-xs text-muted-foreground">
            Categorical calibration needs machine graders that emit labels — none registered yet.
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export function CalibrationView({ startDate, endDate }: { startDate: string; endDate: string }) {
  const [metrics, setMetrics] = useState<CalibrationMetric[]>([]);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const report = await evaluationApi.getCalibration({ start_date: startDate, end_date: endDate });
      setMetrics(report.metrics);
    } catch {
      setMetrics([]);
    } finally {
      setLoading(false);
    }
  }, [startDate, endDate]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  if (loading) {
    return <div className="py-8 text-center text-sm text-muted-foreground">Loading calibration…</div>;
  }

  if (metrics.length === 0) {
    return (
      <Card>
        <CardContent className="py-12 text-center">
          <Scale className="mx-auto mb-4 h-12 w-12 text-muted-foreground" />
          <h3 className="mb-2 text-lg font-medium">No paired machine-human samples</h3>
          <p className="text-sm text-muted-foreground">
            Annotate traces in a queue (optionally overriding machine scores) to feed the calibration panel.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      {metrics.map((m) => (
        <MetricCard key={m.metric_name} metric={m} />
      ))}
    </div>
  );
}
