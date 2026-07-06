"use client";

import { useEffect, useState, useCallback } from "react";
import { api } from "@/lib/api-client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Plus, X } from "lucide-react";

interface ModelOption {
  model_id: string;
  display_name: string;
  provider_name: string;
  capability_badges?: string[];
}

interface ComparisonRow {
  model_id: string;
  display_name: string;
  provider_name: string;
  avg_latency: number;
  ttft: number;
  error_rate: number;
  request_count: number;
  cost: number;
  capability_badges: string[];
}

export default function ModelComparisonPage() {
  const [allModels, setAllModels] = useState<ModelOption[]>([]);
  const [selectedModels, setSelectedModels] = useState<string[]>([]);
  const [comparison, setComparison] = useState<ComparisonRow[]>([]);
  const [days, setDays] = useState<number>(7);
  const [loading, setLoading] = useState(false);

  const fetchModels = useCallback(async () => {
    try {
      const res = await api.get<{ items: ModelOption[] }>("/api/models/catalog");
      setAllModels(res.items || []);
    } catch {
      setAllModels([]);
    }
  }, []);

  const fetchComparison = useCallback(async () => {
    if (selectedModels.length < 2) {
      setComparison([]);
      return;
    }
    setLoading(true);
    try {
      const params = new URLSearchParams({
        model_ids: selectedModels.join(","),
        days: String(days),
      });
      const res = await api.get<ComparisonRow[]>(`/api/monitoring/models/compare?${params}`);
      setComparison(Array.isArray(res) ? res : []);
    } catch {
      setComparison([]);
    } finally {
      setLoading(false);
    }
  }, [selectedModels, days]);

  useEffect(() => {
    fetchModels();
  }, [fetchModels]);

  useEffect(() => {
    fetchComparison();
  }, [fetchComparison]);

  const addModel = (modelId: string) => {
    if (!selectedModels.includes(modelId) && selectedModels.length < 5) {
      setSelectedModels([...selectedModels, modelId]);
    }
  };

  const removeModel = (modelId: string) => {
    setSelectedModels(selectedModels.filter((id) => id !== modelId));
  };

  const getModelName = (modelId: string) => {
    const model = allModels.find((m) => m.model_id === modelId);
    return model?.display_name || modelId;
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Model Comparison</h1>
        <div className="flex items-center gap-3">
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
        </div>
      </div>

      {/* Model Selector */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Select Models to Compare (2-5)</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap gap-2 mb-4">
            {selectedModels.map((modelId) => (
              <Badge key={modelId} variant="secondary" className="gap-1 pr-1">
                {getModelName(modelId)}
                <button onClick={() => removeModel(modelId)} className="ml-1 hover:text-destructive">
                  <X className="h-3 w-3" />
                </button>
              </Badge>
            ))}
            {selectedModels.length === 0 && (
              <span className="text-sm text-muted-foreground">No models selected</span>
            )}
          </div>
          <Select onValueChange={addModel} disabled={selectedModels.length >= 5}>
            <SelectTrigger className="w-[300px]">
              <SelectValue placeholder="Add a model..." />
            </SelectTrigger>
            <SelectContent>
              {allModels
                .filter((m) => !selectedModels.includes(m.model_id))
                .map((m) => (
                  <SelectItem key={m.model_id} value={m.model_id}>
                    {m.display_name} ({m.provider_name})
                  </SelectItem>
                ))}
            </SelectContent>
          </Select>
        </CardContent>
      </Card>

      {/* Comparison Table */}
      {selectedModels.length >= 2 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Performance Comparison</CardTitle>
          </CardHeader>
          <CardContent>
            {loading ? (
              <div className="h-[200px] flex items-center justify-center text-muted-foreground">Loading...</div>
            ) : comparison.length > 0 ? (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Model</TableHead>
                    <TableHead>Provider</TableHead>
                    <TableHead className="text-right">Avg Latency</TableHead>
                    <TableHead className="text-right">TTFT</TableHead>
                    <TableHead className="text-right">Error Rate</TableHead>
                    <TableHead className="text-right">Requests</TableHead>
                    <TableHead className="text-right">Cost</TableHead>
                    <TableHead>Capabilities</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {comparison.map((row) => (
                    <TableRow key={row.model_id}>
                      <TableCell className="font-medium">{row.display_name}</TableCell>
                      <TableCell>{row.provider_name}</TableCell>
                      <TableCell className="text-right">{(row.avg_latency * 1000).toFixed(0)}ms</TableCell>
                      <TableCell className="text-right">{(row.ttft * 1000).toFixed(0)}ms</TableCell>
                      <TableCell className="text-right">{(row.error_rate * 100).toFixed(1)}%</TableCell>
                      <TableCell className="text-right">{row.request_count}</TableCell>
                      <TableCell className="text-right">${row.cost.toFixed(4)}</TableCell>
                      <TableCell>
                        <div className="flex flex-wrap gap-1">
                          {(row.capability_badges || []).map((badge) => (
                            <Badge key={badge} variant="outline" className="text-xs">
                              {badge}
                            </Badge>
                          ))}
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            ) : (
              <div className="h-[200px] flex items-center justify-center text-muted-foreground">
                No comparison data for selected models
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
