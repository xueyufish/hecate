"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";

import { RunActionsPanel } from "@/components/tasks/run-actions-panel";
import { LifecycleBadge } from "@/components/tasks/state-badge";
import { Button } from "@/components/ui/button";
import {
  taskApi,
  taskErrorMessage,
  taskErrorCode,
  type TaskDetail,
} from "@/lib/task-types";

type DetailState =
  | { kind: "loading" }
  | { kind: "error"; code: string | null; message: string }
  | { kind: "ready"; detail: TaskDetail };

function createdLabel(created_at: string | null): string {
  if (!created_at) return "未记录";
  const date = new Date(created_at);
  return Number.isNaN(date.getTime()) ? created_at : date.toLocaleString();
}

export default function TaskDetailPage() {
  const params = useParams<{ id: string }>();
  const taskId = params?.id ?? "";

  const [response, setResponse] = useState<{ taskId: string; state: DetailState }>({
    taskId, state: { kind: "loading" },
  });
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [reloadTick, setReloadTick] = useState(0);
  const state: DetailState = response.taskId === taskId ? response.state : { kind: "loading" };

  useEffect(() => {
    let active = true;
    setResponse({ taskId, state: { kind: "loading" } });
    async function fetchDetail() {
      try {
        const detail = await taskApi.get(taskId);
        if (!active) return;
        setResponse({ taskId, state: { kind: "ready", detail } });
        // The API orders attempts by attempt number. Refresh preserves the
        // user's selection if it still belongs to the current Task.
        setSelectedRunId((current) =>
          current && detail.runs.some((run) => run.run_id === current)
            ? current
            : (detail.runs[detail.runs.length - 1]?.run_id ?? null),
        );
      } catch (err) {
        if (active) setResponse({ taskId, state: {
          kind: "error", code: taskErrorCode(err), message: taskErrorMessage(err),
        } });
      }
    }
    if (taskId) void fetchDetail();
    return () => { active = false; };
  }, [taskId, reloadTick]);

  if (!taskId || state.kind === "loading") {
    return (
      <div className="space-y-4 p-6">
        <p data-testid="task-loading">任务详情加载中…</p>
      </div>
    );
  }
  if (state.kind === "error") {
    return (
      <div className="space-y-4 p-6" data-testid="task-error">
        <p role="alert">任务详情加载失败：{state.code && `[${state.code}] `}{state.message}</p>
        <Button variant="outline" size="sm" onClick={() => setReloadTick((t) => t + 1)}>
          重试
        </Button>
        <div>
          <Link href="/ops-center/tasks" className="text-sm hover:underline">
            ← 返回任务列表
          </Link>
        </div>
      </div>
    );
  }

  const detail = state.detail;

  return (
    <div className="space-y-6 p-6" data-testid="task-detail">
      <div>
        <Link href="/ops-center/tasks" className="text-sm hover:underline">
          ← 返回任务列表
        </Link>
      </div>

      <div className="space-y-1" data-testid="task-summary">
        <h1 className="whitespace-pre-wrap break-words text-xl font-semibold">{detail.goal || "（无目标摘要）"}</h1>
        <div className="flex items-center gap-3 text-sm text-muted-foreground">
          <LifecycleBadge state={detail.lifecycle_state} />
          <span>创建时间：{createdLabel(detail.created_at)}</span>
        </div>
      </div>

      <div className="space-y-2" data-testid="run-switcher">
        <h2 className="font-medium">Run（执行尝试）</h2>
        {detail.runs.length === 0 ? (
          <p data-testid="runs-empty">暂无 Run</p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {detail.runs.map((run) => (
              <Button
                key={run.run_id}
                variant={run.run_id === selectedRunId ? "default" : "outline"}
                size="sm"
                aria-pressed={run.run_id === selectedRunId}
                onClick={() => setSelectedRunId(run.run_id)}
              >
                Run #{run.attempt_no}
              </Button>
            ))}
          </div>
        )}
      </div>

      <div className="space-y-2">
        <h2 className="font-medium">动作记录</h2>
        {selectedRunId ? (
          <RunActionsPanel runId={selectedRunId} />
        ) : (
          <p data-testid="actions-no-run">暂无 Run，无动作可查</p>
        )}
      </div>

      <div>
        <Button variant="outline" size="sm" onClick={() => setReloadTick((t) => t + 1)}>
          刷新任务
        </Button>
      </div>
    </div>
  );
}
