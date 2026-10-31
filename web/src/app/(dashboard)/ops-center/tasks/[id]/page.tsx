"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";

import { TaskApprovalPanel } from "@/components/tasks/task-approval-panel";
import { TaskContinuationPanel } from "@/components/tasks/task-continuation-panel";
import { RunActionsPanel } from "@/components/tasks/run-actions-panel";
import { RunDeliveryPanel } from "@/components/tasks/run-delivery-panel";
import { RunEventsPanel } from "@/components/tasks/run-events-panel";
import { TaskCancelPanel } from "@/components/tasks/task-cancel-panel";
import { TaskCommandsPanel } from "@/components/tasks/task-commands-panel";
import { TaskInputPanel } from "@/components/tasks/task-input-panel";
import { LifecycleBadge } from "@/components/tasks/state-badge";
import { Button } from "@/components/ui/button";
import {
  taskApi,
  taskErrorMessage,
  taskErrorCode,
  type TaskDetail,
} from "@/lib/task-types";

type RefreshError = { code: string | null; message: string };

type DetailState =
  | { kind: "loading" }
  | { kind: "error"; code: string | null; message: string }
  // `refreshError` keeps the last ready detail (and any command receipt the
  // user already holds) on screen when a re-query fails — a refresh failure
  // must not read as "the submission failed" and trigger a new command.
  | { kind: "ready"; detail: TaskDetail; refreshError?: RefreshError };

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
  // Bumped when a command is issued so the receipt list re-reads in place.
  const [commandsRefresh, setCommandsRefresh] = useState(0);
  const state: DetailState = response.taskId === taskId ? response.state : { kind: "loading" };

  useEffect(() => {
    let active = true;
    // A same-Task refresh keeps the current content visible (stale-while-
    // revalidate) instead of flashing to loading and dropping receipts.
    setResponse((prev) =>
      prev.taskId === taskId && prev.state.kind === "ready"
        ? prev
        : { taskId, state: { kind: "loading" } },
    );
    async function fetchDetail() {
      try {
        const detail = await taskApi.get(taskId);
        if (!active) return;
        setResponse({ taskId, state: { kind: "ready", detail } });
        const referencedRun = new URLSearchParams(window.location.search).get("run_id");
        // The API orders attempts by attempt number. Refresh preserves the
        // user's selection if it still belongs to the current Task.
        setSelectedRunId((current) =>
          current && detail.runs.some((run) => run.run_id === current)
            ? current
            : (detail.runs.find((run) => run.run_id === referencedRun)?.run_id
              ?? detail.runs[detail.runs.length - 1]?.run_id ?? null),
        );
      } catch (err) {
        if (!active) return;
        const code = taskErrorCode(err);
        const message = taskErrorMessage(err);
        setResponse((prev) => {
          if (prev.taskId === taskId && prev.state.kind === "ready") {
            return { taskId, state: { ...prev.state, refreshError: { code, message } } };
          }
          return { taskId, state: { kind: "error", code, message } };
        });
      }
    }
    if (taskId) void fetchDetail();
    return () => { active = false; };
  }, [taskId, reloadTick]);

  function refreshAfterCommand() {
    setReloadTick((t) => t + 1);
    setCommandsRefresh((c) => c + 1);
  }

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

      {state.refreshError && (
        <div className="space-y-2 rounded border p-3" data-testid="task-refresh-error" role="alert">
          <p className="text-sm">
            任务刷新失败：{state.refreshError.code && `[${state.refreshError.code}] `}
            {state.refreshError.message}（当前显示最近一次成功读取的内容）
          </p>
          <Button variant="outline" size="sm" onClick={() => setReloadTick((t) => t + 1)}>
            重试刷新
          </Button>
        </div>
      )}

      <div className="space-y-2" data-testid="task-approval-section">
        <h2 className="font-medium">审批决定</h2>
        <TaskApprovalPanel taskId={taskId} detail={detail} onIssued={refreshAfterCommand} />
      </div>

      <div className="space-y-2" data-testid="task-input-section">
        <h2 className="font-medium">补充输入</h2>
        <TaskInputPanel taskId={taskId} detail={detail} onIssued={refreshAfterCommand} />
      </div>

      <div className="space-y-2" data-testid="task-cancel-section">
        <h2 className="font-medium">取消任务</h2>
        <TaskCancelPanel
          taskId={taskId}
          lifecycleState={detail.lifecycle_state}
          revision={detail.revision}
          onIssued={refreshAfterCommand}
        />
      </div>

      {/* Task-scoped read-only trail: rendered outside the Run switcher so a
          run change can never present the trail as the selected run's own
          history. The run's final verdict stays in the delivery panel. */}
      <div className="space-y-2" data-testid="task-continuation-section">
        <h2 className="font-medium">受控续跑（任务级）</h2>
        <TaskContinuationPanel continuation={detail.continuation} />
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

      <div className="space-y-2" data-testid="run-delivery-section">
        <h2 className="font-medium">交付与验收</h2>
        {(() => {
          const selected = detail.runs.find((run) => run.run_id === selectedRunId);
          return selected ? (
            <RunDeliveryPanel run={selected} />
          ) : (
            <p data-testid="delivery-no-run">暂无 Run，无交付与验收可查</p>
          );
        })()}
      </div>

      <div className="space-y-2">
        <h2 className="font-medium">动作记录</h2>
        {selectedRunId ? (
          <RunActionsPanel runId={selectedRunId} />
        ) : (
          <p data-testid="actions-no-run">暂无 Run，无动作可查</p>
        )}
      </div>

      <div className="space-y-2">
        <h2 className="font-medium">事件</h2>
        {selectedRunId ? (
          <RunEventsPanel runId={selectedRunId} onTerminal={() => setReloadTick((t) => t + 1)} />
        ) : (
          <p data-testid="events-no-run">暂无 Run，无事件可查</p>
        )}
      </div>

      {/* Task-scoped: commands are not filtered by the selected Run and work
          without any Run, so the panel mounts outside the Run switcher. */}
      <div className="space-y-2" data-testid="task-commands-section">
        <h2 className="font-medium">控制命令回执</h2>
        <TaskCommandsPanel taskId={taskId} refreshSignal={commandsRefresh} />
      </div>

      <div>
        <Button variant="outline" size="sm" onClick={() => setReloadTick((t) => t + 1)}>
          刷新任务
        </Button>
      </div>
    </div>
  );
}
