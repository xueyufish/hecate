"use client";

import { useEffect, useState, useCallback } from "react";
import { api } from "@/lib/api-client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { BarChart } from "@/components/ui/bar-chart";
import { AlertTriangle, TrendingUp, DollarSign, Target } from "lucide-react";

interface CostTrend {
  date: string;
  model: string;
  cost: number;
  tokens: number;
}

interface BudgetStatus {
  budget_id: string;
  scope: string;
  limit_amount: number;
  spent_amount: number;
  utilization_pct: number;
  status_band: string;
  policy: string;
}

interface Forecast {
  projected_amount: number;
  confidence_low: number;
  confidence_high: number;
  status: string;
  overrun: number;
}

interface Anomaly {
  date: string;
  model: string;
  actual_spend: number;
  expected_spend: number;
  z_score: number;
  severity: string;
}

const STATUS_COLORS: Record<string, string> = {
  healthy: "text-green-600",
  caution: "text-yellow-600",
  warning: "text-orange-600",
  critical: "text-red-600",
  breached: "text-red-700 font-bold",
};

export default function CostAnalysisPage() {
  const [costTrends, setCostTrends] = useState<CostTrend[]>([]);
  const [budgets, setBudgets] = useState<BudgetStatus[]>([]);
  const [forecast, setForecast] = useState<Forecast | null>(null);
  const [anomalies, setAnomalies] = useState<Anomaly[]>([]);
  const [days, setDays] = useState<number>(30);
  const [loading, setLoading] = useState(false);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [trendsRes, budgetsRes, forecastRes, anomaliesRes] = await Promise.all([
        api.get<CostTrend[]>(`/api/monitoring/models/trends?days=${days}`),
        api.get<{ id: string; scope: string; limit_amount: number }[]>("/api/models/cost/budgets").then(async (r) => {
          const budgetList = Array.isArray(r) ? r : [];
          const statuses = await Promise.all(
            budgetList.map((b) =>
              api.get<BudgetStatus>(`/api/models/cost/budgets/${b.id}/status`).catch(() => null)
            )
          );
          return statuses.filter(Boolean) as BudgetStatus[];
        }),
        api.get<Forecast>("/api/models/cost/forecast").catch(() => null),
        api.get<Anomaly[]>("/api/models/cost/anomalies").catch(() => []),
      ]);
      setCostTrends(Array.isArray(trendsRes) ? trendsRes : []);
      setBudgets(budgetsRes);
      setForecast(forecastRes);
      setAnomalies(Array.isArray(anomaliesRes) ? anomaliesRes : []);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Aggregate cost by model for bar chart
  const costByModel: Record<string, number> = {};
  for (const t of costTrends) {
    costByModel[t.model] = (costByModel[t.model] || 0) + t.cost;
  }
  const barData = Object.entries(costByModel)
    .sort(([, a], [, b]) => b - a)
    .slice(0, 10)
    .map(([model, cost]) => ({ name: model, cost: Number(cost.toFixed(4)) }));

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Cost Analysis</h1>
        <div className="flex items-center gap-3">
          <Select value={String(days)} onValueChange={(v) => setDays(Number(v))}>
            <SelectTrigger className="w-[120px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="7">Last 7 days</SelectItem>
              <SelectItem value="30">Last 30 days</SelectItem>
              <SelectItem value="90">Last 90 days</SelectItem>
            </SelectContent>
          </Select>
          <Button variant="outline" size="sm" onClick={fetchData}>
            Refresh
          </Button>
        </div>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-4 gap-4">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium">Total Spend</CardTitle>
            <DollarSign className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              ${costTrends.reduce((s, t) => s + t.cost, 0).toFixed(4)}
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium">Projected Month-End</CardTitle>
            <TrendingUp className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {forecast ? `$${forecast.projected_amount.toFixed(2)}` : "—"}
            </div>
            {forecast && forecast.overrun > 0 && (
              <Badge variant="destructive" className="mt-1">
                +${forecast.overrun.toFixed(2)} over budget
              </Badge>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium">Budget Status</CardTitle>
            <Target className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            {budgets.length > 0 ? (
              <div>
                <div className={`text-2xl font-bold ${STATUS_COLORS[budgets[0].status_band] || ""}`}>
                  {budgets[0].utilization_pct.toFixed(1)}%
                </div>
                <p className="text-xs text-muted-foreground">
                  ${budgets[0].spent_amount.toFixed(2)} / ${budgets[0].limit_amount.toFixed(2)}
                </p>
              </div>
            ) : (
              <div className="text-muted-foreground text-sm">No budget set</div>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium">Anomalies</CardTitle>
            <AlertTriangle className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{anomalies.length}</div>
            {anomalies.length > 0 && (
              <Badge variant="destructive" className="mt-1">
                {anomalies.filter((a) => a.severity === "critical").length} critical
              </Badge>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Cost by Model Bar Chart */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Cost by Model</CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="h-[300px] flex items-center justify-center text-muted-foreground">Loading...</div>
          ) : barData.length > 0 ? (
            <BarChart data={barData} bars={[{ dataKey: "cost", color: "hsl(var(--chart-1))", name: "Cost ($)" }]} height={300} />
          ) : (
            <div className="h-[300px] flex items-center justify-center text-muted-foreground">No cost data</div>
          )}
        </CardContent>
      </Card>

      {/* Budget Utilization */}
      {budgets.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Budget Utilization</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {budgets.map((b) => (
                <div key={b.budget_id} className="flex items-center gap-4">
                  <div className="w-24 text-sm font-medium">{b.scope}</div>
                  <div className="flex-1">
                    <div className="h-3 bg-muted rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full transition-all ${
                          b.utilization_pct >= 100 ? "bg-red-600" :
                          b.utilization_pct >= 80 ? "bg-orange-500" :
                          b.utilization_pct >= 50 ? "bg-yellow-500" : "bg-green-500"
                        }`}
                        style={{ width: `${Math.min(b.utilization_pct, 100)}%` }}
                      />
                    </div>
                  </div>
                  <div className="w-20 text-right text-sm">
                    ${b.spent_amount.toFixed(2)} / ${b.limit_amount.toFixed(2)}
                  </div>
                  <Badge variant={b.utilization_pct >= 80 ? "destructive" : "outline"} className="w-16 justify-center">
                    {b.utilization_pct.toFixed(0)}%
                  </Badge>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Anomaly Timeline */}
      {anomalies.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Anomaly Timeline</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              {anomalies.slice(0, 10).map((a, i) => (
                <div key={i} className="flex items-center gap-3 p-2 rounded-md border">
                  <Badge
                    variant={a.severity === "critical" ? "destructive" : a.severity === "warn" ? "secondary" : "outline"}
                    className="w-16 justify-center"
                  >
                    {a.severity}
                  </Badge>
                  <div className="flex-1 text-sm">{a.model}</div>
                  <div className="text-sm text-muted-foreground">{a.date}</div>
                  <div className="text-sm">
                    ${a.actual_spend.toFixed(4)} <span className="text-muted-foreground">(expected ${a.expected_spend.toFixed(4)})</span>
                  </div>
                  <div className="text-xs text-muted-foreground">z={a.z_score.toFixed(2)}</div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
