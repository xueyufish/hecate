"use client";

import { useEffect, useState, useCallback } from "react";
import { api } from "@/lib/api-client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { BarChart } from "@/components/ui/bar-chart";
import { LineChart } from "@/components/ui/line-chart";
import { MessageSquare, ThumbsUp, ThumbsDown, AlertTriangle, CheckCircle, TrendingUp } from "lucide-react";

interface Overview {
  total_conversations: number;
  scored_conversations: number;
  avg_quality_score: number | null;
  quality_distribution: { low: number; medium: number; high: number };
  feedback_summary: { positive: number; negative: number; total: number };
}

interface Topic {
  topic: string;
  count: number;
  avg_quality: number | null;
}

interface LowQualityConv {
  id: string;
  agent_id: string;
  title: string | null;
  quality_score: number;
  topic: string | null;
  created_at: string | null;
}

interface TrendPoint {
  date: string;
  total: number;
  scored: number;
  avg_quality: number | null;
}

const COLORS = ["hsl(var(--chart-1))", "hsl(var(--chart-2))", "hsl(var(--chart-3))", "hsl(var(--chart-4))", "hsl(var(--chart-5))"];

export default function ConversationAnalyticsPage() {
  const [days, setDays] = useState<number>(7);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [topics, setTopics] = useState<Topic[]>([]);
  const [lowQuality, setLowQuality] = useState<LowQualityConv[]>([]);
  const [trends, setTrends] = useState<TrendPoint[]>([]);
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
      const res = await api.get<Overview>(`/api/ops-center/conversations/overview?${params}`);
      setOverview(res);
    } catch {
      setOverview(null);
    } finally {
      setLoading(false);
    }
  }, [dateRange]);

  const fetchTopics = useCallback(async () => {
    try {
      const { start, end } = dateRange();
      const params = new URLSearchParams({ start_date: start, end_date: end });
      const res = await api.get<Topic[]>(`/api/ops-center/conversations/topics?${params}`);
      setTopics(res);
    } catch {
      setTopics([]);
    }
  }, [dateRange]);

  const fetchLowQuality = useCallback(async () => {
    try {
      const { start, end } = dateRange();
      const params = new URLSearchParams({ threshold: "0.5", start_date: start, end_date: end });
      const res = await api.get<LowQualityConv[]>(`/api/ops-center/conversations/low-quality?${params}`);
      setLowQuality(res);
    } catch {
      setLowQuality([]);
    }
  }, [dateRange]);

  const fetchTrends = useCallback(async () => {
    try {
      const params = new URLSearchParams({ granularity: "daily", days: String(days) });
      const res = await api.get<TrendPoint[]>(`/api/ops-center/conversations/trends?${params}`);
      setTrends(res);
    } catch {
      setTrends([]);
    }
  }, [days]);

  useEffect(() => {
    fetchOverview();
    fetchTopics();
    fetchLowQuality();
    fetchTrends();
  }, [fetchOverview, fetchTopics, fetchLowQuality, fetchTrends]);

  const hasData = overview && overview.total_conversations > 0;
  const feedbackRatio = overview && overview.feedback_summary.total > 0
    ? ((overview.feedback_summary.positive / overview.feedback_summary.total) * 100).toFixed(1)
    : "N/A";

  return (
    <div className="container mx-auto space-y-6 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Conversation Analytics</h1>
          <p className="text-muted-foreground">Monitor conversation quality and user feedback</p>
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
          <Button variant="outline" size="sm" onClick={() => { fetchOverview(); fetchTopics(); fetchLowQuality(); fetchTrends(); }}>
            Refresh
          </Button>
        </div>
      </div>

      {/* Overview Cards */}
      <div className="grid gap-4 md:grid-cols-4">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Total Conversations</CardTitle>
            <MessageSquare className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{overview?.total_conversations ?? 0}</div>
            <p className="text-xs text-muted-foreground">
              {overview?.scored_conversations ?? 0} scored
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Avg Quality Score</CardTitle>
            <TrendingUp className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {overview?.avg_quality_score !== null && overview?.avg_quality_score !== undefined
                ? (overview.avg_quality_score * 100).toFixed(0)
                : "N/A"}
            </div>
            <p className="text-xs text-muted-foreground">
              {overview?.avg_quality_score !== null ? "out of 100" : "no data yet"}
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Feedback Ratio</CardTitle>
            <ThumbsUp className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{feedbackRatio}%</div>
            <p className="text-xs text-muted-foreground">
              {overview?.feedback_summary.total ?? 0} total feedback
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Low Quality</CardTitle>
            <AlertTriangle className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-red-600">{overview?.quality_distribution.low ?? 0}</div>
            <p className="text-xs text-muted-foreground">conversations below 40%</p>
          </CardContent>
        </Card>
      </div>

      {/* Charts */}
      <div className="grid gap-4 md:grid-cols-2">
        {/* Quality Trend */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium">Quality Trend</CardTitle>
          </CardHeader>
          <CardContent>
            {trends.length > 0 ? (
              <LineChart
                data={trends.map((t) => ({
                  date: t.date.slice(5, 10),
                  quality: t.avg_quality !== null ? t.avg_quality * 100 : 0,
                  conversations: t.total,
                }))}
                lines={[
                  { dataKey: "quality", color: COLORS[0], name: "Avg Quality" },
                  { dataKey: "conversations", color: COLORS[1], name: "Conversations" },
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

        {/* Topic Distribution */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium">Topic Distribution</CardTitle>
          </CardHeader>
          <CardContent>
            {topics.length > 0 ? (
              <BarChart
                data={topics.map((t) => ({
                  topic: t.topic,
                  count: t.count,
                }))}
                bars={[
                  { dataKey: "count", color: COLORS[2], name: "Conversations" },
                ]}
                xAxisKey="topic"
                height={250}
              />
            ) : (
              <div className="flex h-[250px] items-center justify-center text-muted-foreground">
                No topic data available
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Low Quality Conversations */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Low Quality Conversations</CardTitle>
        </CardHeader>
        <CardContent>
          {lowQuality.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b">
                    <th className="pb-3 text-left text-sm font-medium text-muted-foreground">Conversation</th>
                    <th className="pb-3 text-left text-sm font-medium text-muted-foreground">Topic</th>
                    <th className="pb-3 text-right text-sm font-medium text-muted-foreground">Quality Score</th>
                    <th className="pb-3 text-right text-sm font-medium text-muted-foreground">Date</th>
                  </tr>
                </thead>
                <tbody>
                  {lowQuality.map((conv) => (
                    <tr key={conv.id} className="border-b last:border-0">
                      <td className="py-3">
                        <div className="font-medium">{conv.title || "Untitled"}</div>
                        <div className="text-xs text-muted-foreground">{conv.id.slice(0, 8)}</div>
                      </td>
                      <td className="py-3">
                        <span className="rounded-full bg-muted px-2 py-1 text-xs">
                          {conv.topic || "unclassified"}
                        </span>
                      </td>
                      <td className="py-3 text-right">
                        <span className="font-medium text-red-600">
                          {(conv.quality_score * 100).toFixed(0)}%
                        </span>
                      </td>
                      <td className="py-3 text-right text-sm text-muted-foreground">
                        {conv.created_at ? new Date(conv.created_at).toLocaleDateString() : ""}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="py-8 text-center text-muted-foreground">
              No low quality conversations found
            </div>
          )}
        </CardContent>
      </Card>

      {/* Empty State */}
      {!loading && !hasData && (
        <Card>
          <CardContent className="py-12 text-center">
            <MessageSquare className="mx-auto mb-4 h-12 w-12 text-muted-foreground" />
            <h3 className="mb-2 text-lg font-medium">No conversation data</h3>
            <p className="text-sm text-muted-foreground">
              Conversation analytics will appear here once agents start having conversations.
            </p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
