"use client";

import { useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronRight, Gauge, ListPlus } from "lucide-react";
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
import {
  AnnotationQueue,
  BreakdownsReport,
  evaluationApi,
  OnlineTaskListItem,
  SessionRollupItem,
  SessionTrace,
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

/** Entry point: enqueue this session's traces into an annotation queue (7.4). */
function AddToQueuePanel({ sessionId }: { sessionId: string }) {
  const [open, setOpen] = useState(false);
  const [queues, setQueues] = useState<AnnotationQueue[]>([]);
  const [queueId, setQueueId] = useState<string>("");
  const [traces, setTraces] = useState<SessionTrace[] | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [message, setMessage] = useState<string | null>(null);

  const toggle = async () => {
    if (!open) {
      try {
        const res = await evaluationApi.listAnnotationQueues();
        setQueues(res.items);
        setQueueId(res.items[0]?.id ?? "");
      } catch {
        setQueues([]);
      }
      try {
        setTraces(await evaluationApi.listSessionTraces(sessionId));
      } catch {
        setTraces([]);
      }
      setSelected(new Set());
      setMessage(null);
    }
    setOpen(!open);
  };

  const add = async () => {
    if (!queueId || selected.size === 0) return;
    const result = await evaluationApi.addAnnotationQueueItems(queueId, [...selected]);
    setMessage(
      `added ${result.created} · already queued ${result.already_present.length} · rejected ${result.rejected.length}`
    );
  };

  return (
    <div className="mt-2 border-t pt-2">
      <Button variant="outline" size="sm" onClick={toggle}>
        <ListPlus className="mr-1 h-3 w-3" /> Add to annotation queue
      </Button>
      {open && (
        <div className="mt-2 space-y-2">
          {queues.length === 0 ? (
            <div className="text-xs text-muted-foreground">No annotation queues — create one first.</div>
          ) : (
            <>
              <Select value={queueId} onValueChange={setQueueId}>
                <SelectTrigger className="w-64">
                  <SelectValue placeholder="pick a queue" />
                </SelectTrigger>
                <SelectContent>
                  {queues.map((q) => (
                    <SelectItem key={q.id} value={q.id}>
                      {q.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {traces === null ? (
                <div className="text-xs text-muted-foreground">Loading traces…</div>
              ) : traces.length === 0 ? (
                <div className="text-xs text-muted-foreground">No root traces in this session.</div>
              ) : (
                <div className="max-h-32 space-y-1 overflow-y-auto">
                  {traces.map((t) => (
                    <label key={t.id} className="flex items-center gap-2 text-xs">
                      <input
                        type="checkbox"
                        checked={selected.has(t.id)}
                        onChange={(e) => {
                          const next = new Set(selected);
                          if (e.target.checked) next.add(t.id);
                          else next.delete(t.id);
                          setSelected(next);
                        }}
                      />
                      <span className="font-mono">{t.trace_id.slice(0, 8)}</span>
                      <span className="text-muted-foreground">{t.status}</span>
                    </label>
                  ))}
                </div>
              )}
              <Button size="sm" variant="outline" onClick={add} disabled={selected.size === 0}>
                Add {selected.size || ""} to queue
              </Button>
            </>
          )}
          {message && <div className="text-xs text-muted-foreground">{message}</div>}
        </div>
      )}
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
                      <AddToQueuePanel sessionId={session.session_id} />
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
