"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { api } from "@/lib/api-client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Users, Wrench, MessageSquare, AlertTriangle, CheckCircle, XCircle, Activity, Clock } from "lucide-react";

interface AgentHealth {
  total_agents: number;
  healthy_count: number;
  warning_count: number;
  critical_count: number;
  fleet_error_rate: number;
  fleet_p95_latency_ms: number;
}

interface ToolAnalytics {
  total_executions: number;
  success_rate: number;
  avg_latency_ms: number;
  p95_latency_ms: number;
  error_count: number;
}

interface ConversationAnalytics {
  total_conversations: number;
  scored_conversations: number;
  avg_quality_score: number | null;
  quality_distribution: { low: number; medium: number; high: number };
  feedback_summary: { positive: number; negative: number; total: number };
}

interface Overview {
  agent_health: AgentHealth | null;
  tool_analytics: ToolAnalytics | null;
  conversation_analytics: ConversationAnalytics | null;
  errors: string[];
}

interface ActivityItem {
  source: string;
  severity: string;
  title: string;
  timestamp: string | null;
  link: string;
}

const SEVERITY_COLORS: Record<string, string> = {
  critical: "bg-red-100 text-red-800",
  warning: "bg-yellow-100 text-yellow-800",
  info: "bg-blue-100 text-blue-800",
};

const SEVERITY_ICONS: Record<string, typeof CheckCircle> = {
  critical: XCircle,
  warning: AlertTriangle,
  info: Activity,
};

export default function OpsCenterPage() {
  const [days, setDays] = useState<number>(7);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [activity, setActivity] = useState<ActivityItem[]>([]);
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
      const res = await api.get<Overview>(`/api/ops-center/overview?${params}`);
      setOverview(res);
    } catch {
      setOverview(null);
    } finally {
      setLoading(false);
    }
  }, [dateRange]);

  const fetchActivity = useCallback(async () => {
    try {
      const { start, end } = dateRange();
      const params = new URLSearchParams({ start_date: start, end_date: end, limit: "20" });
      const res = await api.get<ActivityItem[]>(`/api/ops-center/recent-activity?${params}`);
      setActivity(res);
    } catch {
      setActivity([]);
    }
  }, [dateRange]);

  useEffect(() => {
    fetchOverview();
    fetchActivity();
  }, [fetchOverview, fetchActivity]);

  const hasData = overview && (overview.agent_health || overview.tool_analytics || overview.conversation_analytics);

  return (
    <div className="container mx-auto space-y-6 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Ops Center</h1>
          <p className="text-muted-foreground">Unified operational overview across all subsystems</p>
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
          <Button variant="outline" size="sm" onClick={() => { fetchOverview(); fetchActivity(); }}>
            Refresh
          </Button>
        </div>
      </div>

      {/* Errors banner */}
      {overview && overview.errors.length > 0 && (
        <div className="rounded-md border border-yellow-200 bg-yellow-50 p-3 text-sm text-yellow-800">
          ⚠️ Some data sources unavailable: {overview.errors.join(", ")}
        </div>
      )}

      {/* Summary Cards */}
      <div className="grid gap-4 md:grid-cols-3">
        {/* Agent Health Card */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Agent Health</CardTitle>
            <Users className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            {overview?.agent_health ? (
              <>
                <div className="text-2xl font-bold">{overview.agent_health.total_agents}</div>
                <div className="mt-1 flex gap-2 text-xs">
                  <span className="inline-flex items-center gap-1 text-green-600">
                    <CheckCircle className="h-3 w-3" /> {overview.agent_health.healthy_count} healthy
                  </span>
                  <span className="inline-flex items-center gap-1 text-yellow-600">
                    <AlertTriangle className="h-3 w-3" /> {overview.agent_health.warning_count} warning
                  </span>
                  <span className="inline-flex items-center gap-1 text-red-600">
                    <XCircle className="h-3 w-3" /> {overview.agent_health.critical_count} critical
                  </span>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  Error rate: {(overview.agent_health.fleet_error_rate * 100).toFixed(1)}% · P95: {overview.agent_health.fleet_p95_latency_ms}ms
                </p>
              </>
            ) : (
              <div className="py-4 text-center text-sm text-muted-foreground">Data unavailable</div>
            )}
          </CardContent>
        </Card>

        {/* Tool Analytics Card */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Tool Analytics</CardTitle>
            <Wrench className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            {overview?.tool_analytics ? (
              <>
                <div className="text-2xl font-bold">{overview.tool_analytics.total_executions}</div>
                <p className="text-xs text-muted-foreground">
                  Success rate: {(overview.tool_analytics.success_rate * 100).toFixed(1)}%
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  P95: {overview.tool_analytics.p95_latency_ms}ms · Errors: {overview.tool_analytics.error_count}
                </p>
              </>
            ) : (
              <div className="py-4 text-center text-sm text-muted-foreground">Data unavailable</div>
            )}
          </CardContent>
        </Card>

        {/* Conversation Quality Card */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Conversation Quality</CardTitle>
            <MessageSquare className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            {overview?.conversation_analytics ? (
              <>
                <div className="text-2xl font-bold">{overview.conversation_analytics.total_conversations}</div>
                <p className="text-xs text-muted-foreground">
                  Scored: {overview.conversation_analytics.scored_conversations} · Avg: {overview.conversation_analytics.avg_quality_score !== null ? (overview.conversation_analytics.avg_quality_score * 100).toFixed(0) : "N/A"}%
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  Feedback: {overview.conversation_analytics.feedback_summary.total > 0 ? ((overview.conversation_analytics.feedback_summary.positive / overview.conversation_analytics.feedback_summary.total) * 100).toFixed(0) : "N/A"}% positive
                </p>
              </>
            ) : (
              <div className="py-4 text-center text-sm text-muted-foreground">Data unavailable</div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Recent Activity Feed */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Recent Activity</CardTitle>
        </CardHeader>
        <CardContent>
          {activity.length > 0 ? (
            <div className="space-y-3">
              {activity.map((item, i) => {
                const SeverityIcon = SEVERITY_ICONS[item.severity] || Activity;
                return (
                  <Link key={i} href={item.link} className="flex items-center justify-between rounded-md border p-3 hover:bg-muted">
                    <div className="flex items-center gap-3">
                      <span className={`inline-flex items-center gap-1 rounded-full px-2 py-1 text-xs font-medium ${SEVERITY_COLORS[item.severity]}`}>
                        <SeverityIcon className="h-3 w-3" />
                        {item.severity}
                      </span>
                      <div>
                        <div className="text-sm font-medium">{item.title}</div>
                        <div className="text-xs text-muted-foreground">{item.source}</div>
                      </div>
                    </div>
                    <div className="flex items-center gap-2 text-xs text-muted-foreground">
                      <Clock className="h-3 w-3" />
                      {item.timestamp ? new Date(item.timestamp).toLocaleString() : ""}
                    </div>
                  </Link>
                );
              })}
            </div>
          ) : (
            <div className="py-8 text-center text-muted-foreground">
              <CheckCircle className="mx-auto mb-2 h-8 w-8 text-green-500" />
              All systems operational
            </div>
          )}
        </CardContent>
      </Card>

      {/* Quick Links */}
      <div className="grid gap-4 md:grid-cols-3">
        <Link href="/ops-center/agents">
          <Card className="transition-colors hover:bg-muted">
            <CardContent className="flex items-center justify-between p-4">
              <div className="flex items-center gap-3">
                <Users className="h-5 w-5 text-muted-foreground" />
                <span className="font-medium">Agent Health</span>
              </div>
              <span className="text-muted-foreground">→</span>
            </CardContent>
          </Card>
        </Link>
        <Link href="/ops-center/tools">
          <Card className="transition-colors hover:bg-muted">
            <CardContent className="flex items-center justify-between p-4">
              <div className="flex items-center gap-3">
                <Wrench className="h-5 w-5 text-muted-foreground" />
                <span className="font-medium">Tool Analytics</span>
              </div>
              <span className="text-muted-foreground">→</span>
            </CardContent>
          </Card>
        </Link>
        <Link href="/ops-center/conversations">
          <Card className="transition-colors hover:bg-muted">
            <CardContent className="flex items-center justify-between p-4">
              <div className="flex items-center gap-3">
                <MessageSquare className="h-5 w-5 text-muted-foreground" />
                <span className="font-medium">Conversations</span>
              </div>
              <span className="text-muted-foreground">→</span>
            </CardContent>
          </Card>
        </Link>
      </div>

      {/* Empty State */}
      {!loading && !hasData && (
        <Card>
          <CardContent className="py-12 text-center">
            <Activity className="mx-auto mb-4 h-12 w-12 text-muted-foreground" />
            <h3 className="mb-2 text-lg font-medium">No Ops Center data</h3>
            <p className="text-sm text-muted-foreground">
              Start using agents to generate operational data.
            </p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
