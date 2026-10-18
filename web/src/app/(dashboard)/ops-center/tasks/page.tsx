"use client";

import { useEffect, useState } from "react";

import { TaskListTable } from "@/components/tasks/task-list-table";
import { Button } from "@/components/ui/button";
import {
  taskApi,
  taskErrorMessage,
  taskErrorCode,
  TASK_LIST_PAGE_SIZE,
  type TaskListResponse,
} from "@/lib/task-types";

type ListState =
  | { kind: "loading" }
  | { kind: "error"; code: string | null; message: string }
  | { kind: "ready"; body: TaskListResponse; page: number };

export default function TasksPage() {
  const [page, setPage] = useState(1);
  const [reloadTick, setReloadTick] = useState(0);
  const [response, setResponse] = useState<{ page: number; state: ListState }>({
    page, state: { kind: "loading" },
  });
  const state: ListState = response.page === page ? response.state : { kind: "loading" };

  useEffect(() => {
    let active = true;
    setResponse({ page, state: { kind: "loading" } });
    async function fetchPage() {
      try {
        const body = await taskApi.list(page, TASK_LIST_PAGE_SIZE);
        if (active) setResponse({ page, state: { kind: "ready", body, page } });
      } catch (err) {
        if (active) setResponse({ page, state: {
          kind: "error", code: taskErrorCode(err), message: taskErrorMessage(err),
        } });
      }
    }
    void fetchPage();
    return () => { active = false; };
  }, [page, reloadTick]);

  const total = state.kind === "ready" ? state.body.total : 0;
  const totalPages = Math.max(1, Math.ceil(total / TASK_LIST_PAGE_SIZE));

  return (
    <div className="space-y-4 p-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">任务</h1>
        <Button variant="outline" size="sm" disabled={state.kind === "loading"}
          onClick={() => setReloadTick((t) => t + 1)}>刷新列表</Button>
      </div>

      {state.kind === "loading" && <p data-testid="tasks-loading">任务列表加载中…</p>}
      {state.kind === "error" && (
        <div data-testid="tasks-error">
          <p role="alert">任务列表加载失败：{state.code && `[${state.code}] `}{state.message}</p>
          <Button variant="outline" size="sm" onClick={() => setReloadTick((t) => t + 1)}>
            重试
          </Button>
        </div>
      )}
      {state.kind === "ready" && (
        <>
          {state.body.items.length === 0
            ? <p data-testid="tasks-empty">{state.body.total === 0 ? "暂无任务" : "当前页暂无任务"}</p>
            : <TaskListTable tasks={state.body.items} />}
          <div className="flex items-center gap-3" data-testid="tasks-pagination">
            <span className="text-sm text-muted-foreground">
              共 {total} 条 · 第 {state.page} 页 · 共 {totalPages} 页
            </span>
            <Button
              variant="outline"
              size="sm"
              disabled={state.page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              上一页
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={state.page >= totalPages}
              onClick={() => setPage((p) => p + 1)}
            >
              下一页
            </Button>
          </div>
        </>
      )}
    </div>
  );
}
