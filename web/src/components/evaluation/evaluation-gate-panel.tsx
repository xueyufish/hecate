"use client";

/** Publish evaluation gate configuration panel (7.3a).

Renders the workflow's gate configuration as a small form, persists it via
``evaluationApi.updateWorkflowGate``, and shows the last publish-time report's
gate verdict (signals, deterministic pass rate, regressions) when one is
attached. The publish API itself is gated on this configuration; the panel
only edits the configuration.
*/

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { evaluationApi, type EvaluationGateConfig } from "@/lib/api-client";

export interface GateReportPayload {
  mode: string;
  bypassed_by_force?: boolean;
  deterministic_pass_rate?: number | null;
  deterministic_metric_averages?: Record<string, number>;
  signals?: Array<{ name: string; enabled: boolean; passed: boolean; detail: Record<string, unknown> }>;
  regressions?: Array<{ metric_name: string; baseline: number; candidate: number; drop: number }>;
  verdict?: string;
}

interface EvaluationGatePanelProps {
  workflowId: string;
  currentGate: EvaluationGateConfig | null;
  lastReportGate?: GateReportPayload | null;
}

const SIGNAL_LABELS: Record<string, string> = {
  min_pass_rate: "Minimum deterministic pass rate",
  block_on_regression: "Block on deterministic metric regression",
  block_on_drift: "Block when dataset snapshot drifts",
  require_run: "Require a completed evaluation run",
  require_dataset_version: "Require a named dataset version",
};

function defaultConfig(): EvaluationGateConfig {
  return { mode: "warn", min_pass_rate: 0.9, block_on_regression: true };
}

function parseConfig(raw: EvaluationGateConfig | null): EvaluationGateConfig {
  if (!raw) return { mode: "off" };
  return { ...raw };
}

export function EvaluationGatePanel({
  workflowId,
  currentGate,
  lastReportGate,
}: EvaluationGatePanelProps) {
  const [draft, setDraft] = useState<EvaluationGateConfig>(parseConfig(currentGate));
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<string | null>(null);

  useEffect(() => {
    setDraft(parseConfig(currentGate));
  }, [currentGate]);

  const setMode = (mode: EvaluationGateConfig["mode"]) => {
    if (mode === "off") {
      setDraft({ mode: "off" });
      return;
    }
    // When switching to warn/require, seed with sensible defaults so the
    // user is not staring at an all-disabled form.
    setDraft(mode === "warn" ? defaultConfig() : { ...defaultConfig(), mode: "require" });
  };

  const onSave = async () => {
    setSaving(true);
    try {
      await evaluationApi.updateWorkflowGate(workflowId, draft.mode === "off" ? null : draft);
      setSavedAt(new Date().toLocaleTimeString());
    } finally {
      setSaving(false);
    }
  };

  const onClear = async () => {
    setSaving(true);
    try {
      await evaluationApi.updateWorkflowGate(workflowId, null);
      setDraft({ mode: "off" });
      setSavedAt(new Date().toLocaleTimeString());
    } finally {
      setSaving(false);
    }
  };

  const isOff = draft.mode === "off";
  const isRequire = draft.mode === "require";

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-2 space-y-0">
        <CardTitle>Publish evaluation gate</CardTitle>
        <Badge variant={isOff ? "outline" : isRequire ? "destructive" : "secondary"}>
          {draft.mode}
        </Badge>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center gap-2">
          <Label className="text-sm">Mode</Label>
          <div className="flex gap-1">
            {(["off", "warn", "require"] as const).map((m) => (
              <Button
                key={m}
                size="sm"
                variant={draft.mode === m ? "default" : "outline"}
                onClick={() => setMode(m)}
              >
                {m}
              </Button>
            ))}
          </div>
        </div>

        {!isOff && (
          <div className="space-y-3">
            <div className="flex items-center gap-3">
              <input
                id="gate-min-pass-rate"
                type="checkbox"
                checked={draft.min_pass_rate != null}
                onChange={(e) =>
                  setDraft((d) => ({ ...d, min_pass_rate: e.target.checked ? 0.9 : null }))
                }
              />
              <Label htmlFor="gate-min-pass-rate" className="text-sm">
                {SIGNAL_LABELS.min_pass_rate}
              </Label>
              {draft.min_pass_rate != null && (
                <Input
                  type="number"
                  step="0.05"
                  min={0}
                  max={1}
                  value={draft.min_pass_rate}
                  onChange={(e) =>
                    setDraft((d) => ({ ...d, min_pass_rate: Number(e.target.value) }))
                  }
                  className="w-24"
                />
              )}
            </div>
            {(
              [
                ["block_on_regression", SIGNAL_LABELS.block_on_regression],
                ["block_on_drift", SIGNAL_LABELS.block_on_drift],
                ["require_run", SIGNAL_LABELS.require_run],
                ["require_dataset_version", SIGNAL_LABELS.require_dataset_version],
              ] as const
            ).map(([key, label]) => (
              <div key={key} className="flex items-center gap-3">
                <input
                  id={`gate-${key}`}
                  type="checkbox"
                  checked={Boolean(draft[key])}
                  onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.checked }))}
                />
                <Label htmlFor={`gate-${key}`} className="text-sm">
                  {label}
                </Label>
              </div>
            ))}
          </div>
        )}

        <div className="flex items-center gap-2 pt-2">
          <Button onClick={onSave} disabled={saving}>
            {saving ? "Saving…" : "Save gate"}
          </Button>
          <Button variant="outline" onClick={onClear} disabled={saving}>
            Clear gate (off)
          </Button>
          {savedAt && <span className="text-xs text-muted-foreground">Saved {savedAt}</span>}
        </div>

        {lastReportGate && lastReportGate.mode !== "off" && (
          <div className="mt-4 rounded border p-3">
            <div className="flex items-center justify-between">
              <div className="text-sm font-medium">Last publish verdict</div>
              <Badge variant={lastReportGate.bypassed_by_force ? "secondary" : "default"}>
                {lastReportGate.verdict ?? (lastReportGate.bypassed_by_force ? "bypassed" : "computed")}
              </Badge>
            </div>
            {lastReportGate.deterministic_pass_rate != null && (
              <div className="mt-1 text-xs text-muted-foreground">
                Deterministic pass rate:{" "}
                {(lastReportGate.deterministic_pass_rate * 100).toFixed(1)}%
              </div>
            )}
            <ul className="mt-2 space-y-1 text-xs">
              {(lastReportGate.signals ?? []).map((s) => (
                <li key={s.name} className="flex items-center justify-between gap-2">
                  <span>{SIGNAL_LABELS[s.name] ?? s.name}</span>
                  <Badge variant={s.passed ? "outline" : "destructive"}>
                    {s.passed ? "passed" : "failed"}
                  </Badge>
                </li>
              ))}
            </ul>
            {lastReportGate.regressions && lastReportGate.regressions.length > 0 && (
              <div className="mt-2 text-xs">
                <div className="font-medium">Regressions</div>
                <ul className="space-y-1">
                  {lastReportGate.regressions.map((r, idx) => (
                    <li key={idx}>
                      {r.metric_name}: baseline {r.baseline.toFixed(3)} → candidate{" "}
                      {r.candidate.toFixed(3)} (drop {r.drop.toFixed(3)})
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}