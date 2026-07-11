"use client";

import { useEffect, useState, useCallback } from "react";
import { api } from "@/lib/api-client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { BarChart } from "@/components/ui/bar-chart";
import { LineChart } from "@/components/ui/line-chart";
import { Wrench, TrendingUp, AlertTriangle, Clock, CheckCircle } from "lucide-react";

interface Overview {
  total_executions: number;
  success_rate: number;
  avg_latency_ms: number;
  p95_latency_ms: number;
  unique_tools: number;
  error_count: number;
}

interface TrendPoint {
  date: string;
  total: number;
  errors: number;
  avg_latency_ms: number;
}

interface TopError {
  tool_name: string;
  message: string;
  count: number;
  last_occurrence: string;
}

const COLORS = ["hsl(var(--chart-1))", "hsl(var(--chart-2))", "hsl(var(--chart-3))", "hsl(var(--chart-4))", "hsl(var(--chart-5))"];

export default function ToolAnalyticsPage() {
  const [days, setDays] = useState<number>(7);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [trends, setTrends] = useState<TrendPoint[]>([]);
  const [errors, setErrors] = useState<TopError[]>([]);
  const [loading, setLoading] = useState(false);

  const dateRange = useCallback(() => {
    const end = new Date();
    const start = new Date(end.getTime() - days * 86400000);
    return { start: start.toISOString(), end: end.toISOString() };
  }, [days]);

  const fetchOverview = useCallback(async () => {
    setLoading(true);
    try {
      const { start, end } = dateRange();
      const params = new URLSearchParams({ start_date: start, end_date: end });
      const res = await api.get<Overview>(`/api/ops-center/tools/overview?${params}`);
      setOverview(res);
    } catch {
      setOverview(null);
    } finally {
      setLoading(false);
    }
  }, [dateRange]);

  const fetchTrends = useCallback(async () => {
    try {
      const params = new URLSearchParams({ granularity: "daily", days: String(days) });
      const res = await api.get<TrendPoint[]>(`/api/ops-center/tools/trends?${params}`);
      setTrends(res);
    } catch {
      setTrends([]);
    }
  }, [days]);

  const fetchErrors = useCallback(async () => {
    try {
      const { start, end } = dateRange();
      const params = new URLSearchParams({ limit: "10", start_date: start, end_date: end });
      const res = await api.get<TopError[]>(`/api/ops-center/tools/errors?${params}`);
      setErrors(res);
    } catch {
      setErrors([]);
    }
  }, [dateRange]);

  useEffect(() => {
    fetchOverview();
    fetchTrends();
    fetchErrors();
  }, [fetchOverview, fetchTrends, fetchErrors]);

  const hasData = overview && overview.total_executions > 0;

  return (
    <div className="container mx-auto space-y-6 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Tool Analytics</h1>
          <p className="text-muted-foreground">Monitor tool execution performance and errors</p>
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
          <Button variant="outline" size="sm" onClick={() => { fetchOverview(); fetchTrends(); fetchErrors(); }}>
            Refresh
          </Button>
        </div>
      </div>

      {/* Overview Cards */}
      <div className="grid gap-4 md:grid-cols-4">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Total Executions</CardTitle>
            <Wrench className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{overview?.total_executions ?? 0}</div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Success Rate</CardTitle>
            <CheckCircle className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{overview ? (overview.success_rate * 100).toFixed(1) : 0}%</div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">P95 Latency</CardTitle>
            <Clock className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{overview?.p95_latency_ms ?? 0}ms</div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Errors</CardTitle>
            <AlertTriangle className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{overview?.error_count ?? 0}</div>
          </CardContent>
        </Card>
      </div>

      {/* Charts */}
      <div className="grid gap-4 md:grid-cols-2">
        {/* Trends */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium">Execution Trends</CardTitle>
          </CardHeader>
          <CardContent>
            {trends.length > 0 ? (
              <LineChart
                data={trends.map((t) => ({
                  date: t.date.slice(5, 10),
                  executions: t.total,
                  errors: t.errors,
                }))}
                lines={[
                  { dataKey: "executions", color: COLORS[0], name: "Executions" },
                  { dataKey: "errors", color: COLORS[1], name: "Errors" },
                ]}
                xAxisKey="date"
                height={250}
              />
            ) : (
              <div className="flex h-[250px] items-center justify-center text-muted-foreground">
                No data available
              </div>
            )}
          </CardContent>
        </Card>

        {/* Latency Trend */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium">Latency Trend</CardTitle>
          </CardHeader>
          <CardContent>
            {trends.length > 0 ? (
              <LineChart
                data={trends.map((t) => ({
                  date: t.date.slice(5, 10),
                  latency: t.avg_latency_ms,
                }))}
                lines={[
                  { dataKey: "latency", color: COLORS[2], name: "Avg Latency (ms)" },
                ]}
                xAxisKey="date"
                height={250}
              />
            ) : (
              <div className="flex h-[250px] items-center justify-center text-muted-foreground">
                No data available
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Top Errors */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Top Errors</CardTitle>
        </CardHeader>
        <CardContent>
          {errors.length > 0 ? (
            <div className="space-y-3">
              {errors.map((err, i) => (
                <div key={i} className="flex items-center justify-between rounded-md border p-3">
                  <div className="flex-1">
                    <div className="font-medium">{err.tool_name}</div>
                    <div className="text-sm text-muted-foreground">{err.message}</div>
                  </div>
                  <div className="text-right">
                    <div className="font-bold">{err.count}</div>
                    <div className="text-xs text-muted-foreground">
                      {err.last_occurrence ? new Date(err.last_occurrence).toLocaleDateString() : ""}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="py-8 text-center text-muted-foreground">
              No errors found in this period
            </div>
          )}
        </CardContent>
      </Card>

      {/* Empty State */}
      {!loading && !hasData && (
        <Card>
          <CardContent className="py-12 text-center">
            <Wrench className="mx-auto mb-4 h-12 w-12 text-muted-foreground" />
            <h3 className="mb-2 text-lg font-medium">No tool execution data</h3>
            <p className="text-sm text-muted-foreground">
              Tool analytics will appear here once agents start executing tools.
            </p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
