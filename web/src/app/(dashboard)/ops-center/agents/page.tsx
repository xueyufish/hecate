"use client";

import { useEffect, useState, useCallback } from "react";
import { api } from "@/lib/api-client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { LineChart } from "@/components/ui/line-chart";
import { Activity, AlertTriangle, CheckCircle, Clock, Users, XCircle } from "lucide-react";

interface DegradedAgent {
  agent_id: string;
  agent_name?: string;
  health_status: "healthy" | "warning" | "critical" | "unknown";
  health_score: number | null;
  error_rate: number;
  p95_latency_ms: number;
}

interface FleetOverview {
  total_agents: number;
  healthy_count: number;
  warning_count: number;
  critical_count: number;
  unknown_count: number;
  fleet_error_rate: number;
  fleet_p95_latency_ms: number;
  top_degraded: DegradedAgent[];
}

interface AgentHealth {
  agent_id: string;
  total_sessions: number;
  error_count: number;
  error_rate: number;
  success_rate: number;
  avg_latency_ms: number;
  p95_latency_ms: number;
  last_active_at: string | null;
  health_status: "healthy" | "warning" | "critical" | "unknown";
  health_score: number | null;
  score_breakdown: {
    error_rate_dimension: number;
    latency_dimension: number;
    activity_dimension: number;
    weights: Record<string, number>;
  };
}

interface TrendPoint {
  date: string;
  total_sessions: number;
  errors: number;
  error_rate: number;
  avg_latency_ms: number;
  p95_latency_ms: number;
}

const COLORS = ["hsl(var(--chart-1))", "hsl(var(--chart-2))", "hsl(var(--chart-3))", "hsl(var(--chart-4))", "hsl(var(--chart-5))"];

const STATUS_COLORS: Record<string, string> = {
  healthy: "bg-green-100 text-green-800",
  warning: "bg-yellow-100 text-yellow-800",
  critical: "bg-red-100 text-red-800",
  unknown: "bg-gray-100 text-gray-800",
};

const STATUS_ICONS: Record<string, typeof CheckCircle> = {
  healthy: CheckCircle,
  warning: AlertTriangle,
  critical: XCircle,
  unknown: Activity,
};

export default function AgentHealthPage() {
  const [days, setDays] = useState<number>(7);
  const [overview, setOverview] = useState<FleetOverview | null>(null);
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null);
  const [agentHealth, setAgentHealth] = useState<AgentHealth | null>(null);
  const [agentTrends, setAgentTrends] = useState<TrendPoint[]>([]);
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
      const res = await api.get<FleetOverview>(`/api/ops-center/agents/overview?${params}`);
      setOverview(res);
    } catch {
      setOverview(null);
    } finally {
      setLoading(false);
    }
  }, [dateRange]);

  const fetchAgentHealth = useCallback(async (agentId: string) => {
    try {
      const { start, end } = dateRange();
      const params = new URLSearchParams({ start_date: start, end_date: end });
      const res = await api.get<AgentHealth>(`/api/ops-center/agents/${agentId}/health?${params}`);
      setAgentHealth(res);
    } catch {
      setAgentHealth(null);
    }
  }, [dateRange]);

  const fetchAgentTrends = useCallback(async (agentId: string) => {
    try {
      const params = new URLSearchParams({ granularity: "daily", days: String(days) });
      const res = await api.get<TrendPoint[]>(`/api/ops-center/agents/${agentId}/trends?${params}`);
      setAgentTrends(res);
    } catch {
      setAgentTrends([]);
    }
  }, [days]);

  useEffect(() => {
    fetchOverview();
  }, [fetchOverview]);

  useEffect(() => {
    if (selectedAgent) {
      fetchAgentHealth(selectedAgent);
      fetchAgentTrends(selectedAgent);
    }
  }, [selectedAgent, fetchAgentHealth, fetchAgentTrends]);

  const hasData = overview && overview.total_agents > 0;

  const handleAgentClick = (agentId: string) => {
    setSelectedAgent(agentId);
  };

  const handleBack = () => {
    setSelectedAgent(null);
    setAgentHealth(null);
    setAgentTrends([]);
  };

  // Agent detail view
  if (selectedAgent) {
    return (
      <div className="container mx-auto space-y-6 p-6">
        <div className="flex items-center gap-4">
          <Button variant="outline" size="sm" onClick={handleBack}>
            Back to Fleet
          </Button>
          <div>
            <h1 className="text-2xl font-bold">Agent Health Detail</h1>
            <p className="text-muted-foreground">{selectedAgent}</p>
          </div>
        </div>

        {/* Agent Health Cards */}
        <div className="grid gap-4 md:grid-cols-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Health Score</CardTitle>
              <Activity className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">
                {agentHealth?.health_score ?? "N/A"}
              </div>
              {agentHealth && (
                <span className={`inline-flex items-center rounded-full px-2 py-1 text-xs font-medium ${STATUS_COLORS[agentHealth.health_status]}`}>
                  {agentHealth.health_status}
                </span>
              )}
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Sessions</CardTitle>
              <Users className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{agentHealth?.total_sessions ?? 0}</div>
              <p className="text-xs text-muted-foreground">
                {agentHealth ? `${(agentHealth.success_rate * 100).toFixed(1)}% success` : ""}
              </p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">P95 Latency</CardTitle>
              <Clock className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{agentHealth?.p95_latency_ms ?? 0}ms</div>
              <p className="text-xs text-muted-foreground">
                {agentHealth ? `avg ${agentHealth.avg_latency_ms}ms` : ""}
              </p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Error Rate</CardTitle>
              <AlertTriangle className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">
                {agentHealth ? `${(agentHealth.error_rate * 100).toFixed(1)}%` : "0%"}
              </div>
              <p className="text-xs text-muted-foreground">
                {agentHealth ? `${agentHealth.error_count} errors` : ""}
              </p>
            </CardContent>
          </Card>
        </div>

        {/* Score Breakdown */}
        {agentHealth?.score_breakdown && (
          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-medium">Score Breakdown</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid gap-4 md:grid-cols-3">
                <div className="rounded-md border p-3">
                  <div className="text-sm text-muted-foreground">Error Rate Dimension</div>
                  <div className="text-lg font-bold">{agentHealth.score_breakdown.error_rate_dimension}</div>
                  <div className="text-xs text-muted-foreground">
                    weight: {(agentHealth.score_breakdown.weights.error_rate * 100).toFixed(0)}%
                  </div>
                </div>
                <div className="rounded-md border p-3">
                  <div className="text-sm text-muted-foreground">Latency Dimension</div>
                  <div className="text-lg font-bold">{agentHealth.score_breakdown.latency_dimension}</div>
                  <div className="text-xs text-muted-foreground">
                    weight: {(agentHealth.score_breakdown.weights.latency * 100).toFixed(0)}%
                  </div>
                </div>
                <div className="rounded-md border p-3">
                  <div className="text-sm text-muted-foreground">Activity Dimension</div>
                  <div className="text-lg font-bold">{agentHealth.score_breakdown.activity_dimension}</div>
                  <div className="text-xs text-muted-foreground">
                    weight: {(agentHealth.score_breakdown.weights.activity * 100).toFixed(0)}%
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Trends Charts */}
        <div className="grid gap-4 md:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-medium">Session Trends</CardTitle>
            </CardHeader>
            <CardContent>
              {agentTrends.length > 0 ? (
                <LineChart
                  data={agentTrends.map((t) => ({
                    date: t.date.slice(5, 10),
                    sessions: t.total_sessions,
                    errors: t.errors,
                  }))}
                  lines={[
                    { dataKey: "sessions", color: COLORS[0], name: "Sessions" },
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
          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-medium">Latency Trend</CardTitle>
            </CardHeader>
            <CardContent>
              {agentTrends.length > 0 ? (
                <LineChart
                  data={agentTrends.map((t) => ({
                    date: t.date.slice(5, 10),
                    latency: t.avg_latency_ms,
                    p95: t.p95_latency_ms,
                  }))}
                  lines={[
                    { dataKey: "latency", color: COLORS[2], name: "Avg Latency (ms)" },
                    { dataKey: "p95", color: COLORS[3], name: "P95 Latency (ms)" },
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
      </div>
    );
  }

  // Fleet overview
  return (
    <div className="container mx-auto space-y-6 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Agent Health</h1>
          <p className="text-muted-foreground">Monitor agent fleet health and performance</p>
        </div>
        <div className="flex items-center gap-2">
          <Select value={String(days)} onValueChange={(v) => setDays(Number(v))}>
            <SelectTrigger className="w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="1">Last 24h</SelectItem>
              <SelectItem value="7">Last 7 days</SelectItem>
              <SelectItem value="30">Last 30 days</SelectItem>
            </SelectContent>
          </Select>
          <Button variant="outline" size="sm" onClick={fetchOverview}>
            Refresh
          </Button>
        </div>
      </div>

      {/* Fleet Status Cards */}
      <div className="grid gap-4 md:grid-cols-4">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Healthy</CardTitle>
            <CheckCircle className="h-4 w-4 text-green-600" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-green-600">{overview?.healthy_count ?? 0}</div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Warning</CardTitle>
            <AlertTriangle className="h-4 w-4 text-yellow-600" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-yellow-600">{overview?.warning_count ?? 0}</div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Critical</CardTitle>
            <XCircle className="h-4 w-4 text-red-600" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-red-600">{overview?.critical_count ?? 0}</div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Total Agents</CardTitle>
            <Users className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{overview?.total_agents ?? 0}</div>
            <p className="text-xs text-muted-foreground">
              {overview ? `${(overview.fleet_error_rate * 100).toFixed(1)}% fleet error rate` : ""}
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Top Degraded Agents Table */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Top Degraded Agents</CardTitle>
        </CardHeader>
        <CardContent>
          {overview && overview.top_degraded.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b">
                    <th className="pb-3 text-left text-sm font-medium text-muted-foreground">Agent</th>
                    <th className="pb-3 text-left text-sm font-medium text-muted-foreground">Status</th>
                    <th className="pb-3 text-right text-sm font-medium text-muted-foreground">Score</th>
                    <th className="pb-3 text-right text-sm font-medium text-muted-foreground">Error Rate</th>
                    <th className="pb-3 text-right text-sm font-medium text-muted-foreground">P95 Latency</th>
                    <th className="pb-3 text-right text-sm font-medium text-muted-foreground">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {overview.top_degraded.map((agent) => {
                    const StatusIcon = STATUS_ICONS[agent.health_status] || Activity;
                    return (
                      <tr key={agent.agent_id} className="border-b last:border-0">
                        <td className="py-3">
                          <div className="font-medium">{agent.agent_name || agent.agent_id.slice(0, 8)}</div>
                          <div className="text-xs text-muted-foreground">{agent.agent_id}</div>
                        </td>
                        <td className="py-3">
                          <span className={`inline-flex items-center gap-1 rounded-full px-2 py-1 text-xs font-medium ${STATUS_COLORS[agent.health_status]}`}>
                            <StatusIcon className="h-3 w-3" />
                            {agent.health_status}
                          </span>
                        </td>
                        <td className="py-3 text-right font-medium">
                          {agent.health_score ?? "N/A"}
                        </td>
                        <td className="py-3 text-right">
                          {(agent.error_rate * 100).toFixed(1)}%
                        </td>
                        <td className="py-3 text-right">
                          {agent.p95_latency_ms}ms
                        </td>
                        <td className="py-3 text-right">
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => handleAgentClick(agent.agent_id)}
                          >
                            Details
                          </Button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="py-8 text-center text-muted-foreground">
              No degraded agents found
            </div>
          )}
        </CardContent>
      </Card>

      {/* Empty State */}
      {!loading && !hasData && (
        <Card>
          <CardContent className="py-12 text-center">
            <Activity className="mx-auto mb-4 h-12 w-12 text-muted-foreground" />
            <h3 className="mb-2 text-lg font-medium">No agent health data</h3>
            <p className="text-sm text-muted-foreground">
              Agent health metrics will appear here once agents start executing sessions.
            </p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
