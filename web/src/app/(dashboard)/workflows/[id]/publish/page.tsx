"use client";

/** Workflow publish page (7.3 + 7.3a).

Lives at ``/workflows/{id}/publish``. Shows the workflow's evaluation
gate configuration, the last publish-time report's gate verdict (when a
report is available), and a publish action that surfaces the gate
contract: a ``require`` gate blocks the publish with a 409 carrying the
gate verdict; ``force=true`` overrides the gate with an audit entry.
*/

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, ShieldCheck, AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { api, evaluationApi, type EvaluationGateConfig } from "@/lib/api-client";
import { EvaluationGatePanel, type GateReportPayload } from "@/components/evaluation/evaluation-gate-panel";

interface WorkflowDetail {
  id: string;
  name: string;
  current_version: number;
  published_version: number | null;
  evaluation_gate?: EvaluationGateConfig | null;
  evaluation_report?: {
    evaluation_status?: string;
    gate?: GateReportPayload;
    dataset_version?: { id: string; name: string; content_hash: string };
  } | null;
}

export default function WorkflowPublishPage() {
  const params = useParams();
  const workflowId = params.id as string;

  const [workflow, setWorkflow] = useState<WorkflowDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [targetVersion, setTargetVersion] = useState<number | null>(null);
  const [publishing, setPublishing] = useState(false);
  const [publishError, setPublishError] = useState<string | null>(null);
  const [publishResult, setPublishResult] = useState<unknown>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const detail = await api.get<WorkflowDetail>(`/api/workflows/${workflowId}`);
      setWorkflow(detail);
      setTargetVersion(detail.current_version);
    } finally {
      setLoading(false);
    }
  }, [workflowId]);

  useEffect(() => {
    reload();
  }, [reload]);

  const doPublish = async (force: boolean) => {
    if (targetVersion == null) return;
    setPublishing(true);
    setPublishError(null);
    try {
      const result = await evaluationApi.publishWorkflowVersion(
        workflowId,
        targetVersion,
        force ? { force: true } : undefined,
      );
      setPublishResult(result);
      await reload();
    } catch (err: unknown) {
      const apiErr = err as { detail?: { error?: { code?: string; message?: string } } };
      setPublishError(
        apiErr?.detail?.error?.message
          ? `[${apiErr.detail.error.code}] ${apiErr.detail.error.message}`
          : String(err),
      );
    } finally {
      setPublishing(false);
    }
  };

  if (loading) return <div className="text-muted-foreground">Loading…</div>;
  if (!workflow) return null;

  const isGateOn = workflow.evaluation_gate != null && workflow.evaluation_gate.mode !== "off";

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-6">
      <div className="flex items-center gap-2">
        <Link href={`/workflows/${workflowId}`}>
          <Button variant="ghost" size="sm">
            <ArrowLeft className="mr-1 h-4 w-4" />
            Back to editor
          </Button>
        </Link>
        <h1 className="text-xl font-semibold">{workflow.name} — publish</h1>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Publish version</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex items-center gap-2 text-sm">
            <span>Current:</span>
            <Badge variant="outline">v{workflow.current_version}</Badge>
            <span className="ml-4">Published:</span>
            <Badge variant="outline">
              {workflow.published_version != null ? `v${workflow.published_version}` : "—"}
            </Badge>
          </div>
          <div className="flex items-center gap-2">
            <label className="text-sm" htmlFor="target-version">
              Publish version
            </label>
            <input
              id="target-version"
              type="number"
              min={1}
              max={workflow.current_version}
              value={targetVersion ?? ""}
              onChange={(e) => setTargetVersion(Number(e.target.value) || null)}
              className="w-20 rounded border px-2 py-1 text-sm"
            />
          </div>
          <div className="flex items-center gap-2 pt-2">
            <Button
              onClick={() => doPublish(false)}
              disabled={publishing || targetVersion == null}
            >
              {publishing ? "Publishing…" : "Publish"}
            </Button>
            {isGateOn && (
              <Button
                variant="destructive"
                onClick={() => doPublish(true)}
                disabled={publishing || targetVersion == null}
              >
                {publishing ? "…" : "Force bypass & publish"}
              </Button>
            )}
          </div>
          {publishError && (
            <div className="mt-2 flex items-start gap-2 rounded border border-red-200 bg-red-50 p-2 text-sm text-red-700">
              <AlertTriangle className="mt-0.5 h-4 w-4" />
              <div>{publishError}</div>
            </div>
          )}
          {publishResult != null && (
            <div className="mt-2 flex items-start gap-2 rounded border border-green-200 bg-green-50 p-2 text-sm text-green-700">
              <ShieldCheck className="mt-0.5 h-4 w-4" />
              <div>Publish succeeded.</div>
            </div>
          )}
        </CardContent>
      </Card>

      <EvaluationGatePanel
        workflowId={workflowId}
        currentGate={workflow.evaluation_gate ?? null}
        lastReportGate={workflow.evaluation_report?.gate ?? null}
      />
    </div>
  );
}