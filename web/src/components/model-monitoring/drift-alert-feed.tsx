"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { AlertTriangle, TrendingDown } from "lucide-react";

interface DriftEvent {
  model_id: string;
  metric: string;
  drift_detected: boolean;
  current_value: number;
  baseline_mean: number;
  z_score: number;
  severity: string;
}

interface DriftAlertFeedProps {
  driftEvents: DriftEvent[];
}

export function DriftAlertFeed({ driftEvents }: DriftAlertFeedProps) {
  if (driftEvents.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Drift Alerts</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-center justify-center h-24 text-muted-foreground text-sm">
            No drift detected
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base flex items-center gap-2">
          Drift Alerts
          <Badge variant="destructive">{driftEvents.length}</Badge>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="space-y-2">
          {driftEvents.map((event, i) => (
            <div key={i} className="flex items-center gap-3 p-2 rounded-md border">
              <AlertTriangle className={`h-4 w-4 ${event.severity === "critical" ? "text-red-600" : "text-orange-500"}`} />
              <div className="flex-1">
                <div className="text-sm font-medium">{event.model_id}</div>
                <div className="text-xs text-muted-foreground">
                  {event.metric}: {event.current_value.toFixed(4)} (baseline: {event.baseline_mean.toFixed(4)})
                </div>
              </div>
              <Badge variant={event.severity === "critical" ? "destructive" : "secondary"}>
                {event.severity}
              </Badge>
              <div className="text-xs text-muted-foreground">z={event.z_score.toFixed(2)}</div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
