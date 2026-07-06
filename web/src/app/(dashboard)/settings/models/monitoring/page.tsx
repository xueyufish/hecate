"use client";

import { useEffect, useState, useCallback } from "react";
import { api } from "@/lib/api-client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { LineChart } from "@/components/ui/line-chart";
import { Activity, TrendingUp, AlertTriangle, DollarSign } from "lucide-react";

interface ModelOption {
  model_id: string;
  display_name: string;
  provider_name: string;
}

interface PerformancePoint {
  date: string;
  avg_latency: number;
  ttft: number;
  error_rate: number;
  request_count: number;
  cost: number;
}

interface DriftResult {
  model_id: string;
  metric: string;
  drift_detected: boolean;
  current_value: number;
  baseline_mean: number;
  z_score: number;
  severity: string;
}

const COLORS = ["hsl(var(--chart-1))", "hsl(var(--chart-2))", "hsl(var(--chart-3))", "hsl(var(--chart-4))", "hsl(var(--chart-5))"];

export default function ModelMonitoringPage() {
  const [models, setModels] = useState<ModelOption[]>([]);
  const [selectedModel, setSelectedModel] = useState<string>("");
  const [days, setDays] = useState<number>(7);
  const [performance, setPerformance] = useState<PerformancePoint[]>([]);
  const [drift, setDrift] = useState<DriftResult | null>(null);
  const [loading, setLoading] = useState(false);

  const fetchModels = useCallback(async () => {
    try {
      const res = await api.get<{ items: { model_id: string; display_name: string; provider_name: string }[] }>("/api/models/catalog");
      setModels(res.items || []);
      if (res.items?.length > 0 && !selectedModel) {
        setSelectedModel(res.items[0].model_id);
      }
    } catch {
      setModels([]);
    }
  }, [selectedModel]);

  const fetchPerformance = useCallback(async () => {
    if (!selectedModel) return;
    setLoading(true);
    try {
      const end = new Date();
      const start = new Date(end.getTime() - days * 86400000);
      const params = new URLSearchParams({
        start_date: start.toISOString(),
        end_date: end.toISOString(),
        granularity: "daily",
      });
      const res = await api.get<{ timeseries: PerformancePoint[] }>(
        `/api/monitoring/models/${selectedModel}/performance?${params}`
      );
      setPerformance(res.timeseries || []);
    } catch {
      setPerformance([]);
    } finally {
      setLoading(false);
    }
  }, [selectedModel, days]);

  const fetchDrift = useCallback(async () => {
    if (!selectedModel) return;
    try {
      const res = await api.get<DriftResult>(`/api/monitoring/models/${selectedModel}/drift?metric=avg_latency`);
      setDrift(res);
    } catch {
      setDrift(null);
    }
  }, [selectedModel]);

  useEffect(() => {
    fetchModels();
  }, [fetchModels]);

  useEffect(() => {
    if (selectedModel) {
      fetchPerformance();
      fetchDrift();
    }
  }, [selectedModel, days, fetchPerformance, fetchDrift]);

  const driftBadge = drift?.drift_detected ? (
    <Badge variant="destructive" className="gap-1">
      <AlertTriangle className="h-3 w-3" />
      Drift: {drift.severity} (z={drift.z_score.toFixed(2)})
    </Badge>
  ) : (
    <Badge variant="outline" className="text-green-600">No Drift</Badge>
  );

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Model Monitoring</h1>
        <div className="flex items-center gap-3">
          <Select value={selectedModel} onValueChange={setSelectedModel}>
            <SelectTrigger className="w-[220px]">
              <SelectValue placeholder="Select model" />
            </SelectTrigger>
            <SelectContent>
              {models.map((m) => (
                <SelectItem key={m.model_id} value={m.model_id}>
                  {m.display_name} ({m.provider_name})
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={String(days)} onValueChange={(v) => setDays(Number(v))}>
            <SelectTrigger className="w-[120px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="7">Last 7 days</SelectItem>
              <SelectItem value="14">Last 14 days</SelectItem>
              <SelectItem value="30">Last 30 days</SelectItem>
            </SelectContent>
          </Select>
          {driftBadge}
        </div>
      </div>

      <div className="grid grid-cols-4 gap-4">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium">Avg Latency</CardTitle>
            <Activity className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {performance.length > 0 ? `${(performance[performance.length - 1].avg_latency * 1000).toFixed(0)}ms` : "—"}
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium">Error Rate</CardTitle>
            <AlertTriangle className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {performance.length > 0 ? `${(performance[performance.length - 1].error_rate * 100).toFixed(1)}%` : "—"}
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium">Requests</CardTitle>
            <TrendingUp className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {performance.length > 0 ? performance[performance.length - 1].request_count : "—"}
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium">Cost</CardTitle>
            <DollarSign className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {performance.length > 0 ? `$${performance[performance.length - 1].cost.toFixed(4)}` : "—"}
            </div>
          </CardContent>
        </Card>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Latency Trend</CardTitle>
          </CardHeader>
          <CardContent>
            {loading ? (
              <div className="h-[300px] flex items-center justify-center text-muted-foreground">Loading...</div>
            ) : (
              <LineChart
                data={performance.map((p) => ({ ...p, avg_latency: p.avg_latency * 1000, ttft: p.ttft * 1000 }))}
                lines={[
                  { dataKey: "avg_latency", color: "hsl(var(--chart-1))", name: "Avg Latency (ms)" },
                  { dataKey: "ttft", color: "hsl(var(--chart-2))", name: "TTFT (ms)" },
                ]}
                height={300}
              />
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Error Rate Trend</CardTitle>
          </CardHeader>
          <CardContent>
            {loading ? (
              <div className="h-[300px] flex items-center justify-center text-muted-foreground">Loading...</div>
            ) : (
              <LineChart
                data={performance.map((p) => ({ ...p, error_rate: p.error_rate * 100 }))}
                lines={[{ dataKey: "error_rate", color: "hsl(var(--chart-3))", name: "Error Rate (%)" }]}
                height={300}
              />
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Cost Trend</CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="h-[250px] flex items-center justify-center text-muted-foreground">Loading...</div>
          ) : (
            <LineChart
              data={performance}
              lines={[{ dataKey: "cost", color: "hsl(var(--chart-4))", name: "Cost ($)" }]}
              height={250}
            />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
