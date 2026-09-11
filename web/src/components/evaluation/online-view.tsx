"use client";

import { useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronRight, Gauge } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { BarChart } from "@/components/ui/bar-chart";
import { Badge } from "@/components/ui/badge";
import {
  BreakdownsReport,
  evaluationApi,
  OnlineTaskListItem,
  SessionRollupItem,
} from "@/lib/api-client";

const COLORS = [
  "hsl(var(--chart-1))",
  "hsl(var(--chart-2))",
  "hsl(var(--chart-3))",
  "hsl(var(--chart-4))",
  "hsl(var(--chart-5))",
];

interface OnlineViewProps {
  startDate: string;
  endDate: string;
}

const METRIC_COLORS: Record<string, string> = {};
function colorFor(metric: string, index: number): string {
  if (!METRIC_COLORS[metric]) {
    METRIC_COLORS[metric] = COLORS[index % COLORS.length];
  }
  return METRIC_COLORS[metric];
}

/** Sampling-budget strip: per-task counters against the configured cost guards. */
function BudgetStrip({ task }: { task: OnlineTaskListItem }) {
  const sampled = task.metrics.sampled ?? 0;
  const scored = task.metrics.scored ?? 0;
  const errors = task.metrics.errors ?? 0;
  const cap = task.config.max_traces_per_cycle;
  return (
    <div
      className="flex flex-wrap items-center gap-3 rounded-md border p-3"
      title={`sampling_rate=${task.config.sampling_rate ?? "n/a"}, max_traces_per_cycle=${cap ?? "n/a"}`}
    >
      <Gauge className="h-4 w-4 text-muted-foreground" />
      <span className="text-sm font-medium">{task.name}</span>
      <Badge variant={task.status === "active" ? "default" : "outline"}>{task.status}</Badge>
      <span className="text-xs text-muted-foreground">
        sampled {sampled}
        {cap !== undefined ? ` / cap ${cap} per cycle` : ""} · scored {scored} · errors {errors}
      </span>
    </div>
  );
}

export function OnlineView({ startDate, endDate }: OnlineViewProps) {
  const [tasks, setTasks] = useState<OnlineTaskListItem[]>([]);
  const [breakdown, setBreakdown] = useState<BreakdownsReport | null>(null);
  const [sessions, setSessions] = useState<SessionRollupItem[]>([]);
  const [expandedSession, setExpandedSession] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const taskRes = await evaluationApi.listOnlineTasks();
      setTasks(taskRes.items);
    } catch {
      setTasks([]);
    }
    try {
      setBreakdown(
        await evaluationApi.getBreakdowns({ group_by: "agent", start_date: startDate, end_date: endDate })
      );
    } catch {
      setBreakdown(null);
    }
    try {
      const sessionRes = await evaluationApi.getSessions({
        start_date: startDate,
        end_date: endDate,
        page_size: 20,
      });
      setSessions(sessionRes.items);
    } catch {
      setSessions([]);
    }
  }, [startDate, endDate]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const metricNames = [...new Set(breakdown?.groups.flatMap((g) => g.metrics.map((m) => m.metric_name)) ?? [])];
  const chartData = (breakdown?.groups ?? []).map((g) => ({
    name: g.key.slice(0, 8),
    ...Object.fromEntries(g.metrics.map((m) => [m.metric_name, Number((m.avg * 100).toFixed(1))])),
  }));

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Sampling Budget</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {tasks.length > 0 ? (
            tasks.map((task) => <BudgetStrip key={task.id} task={task} />)
          ) : (
            <div className="py-6 text-center text-sm text-muted-foreground">
              No online evaluation tasks. Enable one under evaluation tasks to start sampling
              production traces.
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Score by Agent × Metric (%)</CardTitle>
        </CardHeader>
        <CardContent>
          {chartData.length > 0 ? (
            <BarChart
              data={chartData}
              bars={metricNames.map((metric, i) => ({
                dataKey: metric,
                color: colorFor(metric, i),
                name: metric,
              }))}
              xAxisKey="name"
              height={250}
            />
          ) : (
            <div className="flex h-[250px] items-center justify-center text-muted-foreground">
              No online scores in this window
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Sessions</CardTitle>
        </CardHeader>
        <CardContent>
          {sessions.length > 0 ? (
            <div className="space-y-2">
              {sessions.map((session) => (
                <div key={session.session_id} className="rounded-md border">
                  <button
                    className="flex w-full items-center justify-between p-3 text-left"
                    onClick={() =>
                      setExpandedSession(expandedSession === session.session_id ? null : session.session_id)
                    }
                  >
                    <span className="flex items-center gap-2">
                      {expandedSession === session.session_id ? (
                        <ChevronDown className="h-4 w-4" />
                      ) : (
                        <ChevronRight className="h-4 w-4" />
                      )}
                      <span className="font-mono text-xs">{session.session_id.slice(0, 8)}</span>
                      <span className="text-xs text-muted-foreground">
                        {session.trace_count} traces · {session.metrics.map((m) => `${m.metric_name} ${(m.avg * 100).toFixed(0)}%`).join(", ")}
                      </span>
                    </span>
                    <span className="text-xs text-muted-foreground">
                      {new Date(session.last_scored_at).toLocaleDateString()}
                    </span>
                  </button>
                  {expandedSession === session.session_id && (
                    <div className="border-t px-3 py-2 text-xs text-muted-foreground">
                      {session.metrics.length > 0 ? (
                        session.metrics.map((m) => (
                          <div key={m.metric_name} className="flex justify-between py-0.5">
                            <span>{m.metric_name}</span>
                            <span>
                              avg {(m.avg * 100).toFixed(1)}% over {m.count} scores
                            </span>
                          </div>
                        ))
                      ) : (
                        <div>All scores for this session errored out.</div>
                      )}
                      <div className="mt-1 font-mono">session {session.session_id}</div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <div className="py-6 text-center text-sm text-muted-foreground">
              No scored sessions in this window.
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
