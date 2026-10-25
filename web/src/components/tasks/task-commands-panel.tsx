"use client";

// Read-only Task-scoped control-command receipt panel. Receipt states render
// verbatim as recorded by the server: the list read never advances a command,
// the panel never derives expiry from local time, and a command's state never
// rewrites Task/Run lifecycle presentation. Only the paged list endpoint is
// queried; the single-command read (lazy expiry) is intentionally unused.

import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import {
  commandKindLabel, commandStateLabel, TASK_COMMANDS_PAGE_SIZE,
  taskCommandsApi, type CommandBackendRef, type TaskCommandItem, type TaskCommandsPage,
} from "@/lib/task-command-types";
import { taskErrorCode, taskErrorMessage } from "@/lib/task-types";

type Position = { offset: number; prevOffsets: number[] };

const FIRST_POSITION: Position = { offset: 0, prevOffsets: [0] };

type PanelState =
  | { kind: "loading" }
  | { kind: "error"; pos: Position; code: string | null; message: string }
  // `pos` is the position this ready page was loaded for; it keeps refresh and
  // retry on the current page instead of resetting to the first one.
  | { kind: "ready"; page: TaskCommandsPage; pos: Position };

function backendRefText(ref: CommandBackendRef): string {
  return `${ref.issuer_domain}/${ref.id}`;
}

function timeLabel(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function refCell(value: CommandBackendRef | null, emptyLabel: string): string {
  return value ? backendRefText(value) : emptyLabel;
}

function CommandRow({
  item, expanded, onToggle,
}: {
  item: TaskCommandItem;
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <>
      <TableRow data-testid="task-command-row">
        <TableCell>{commandKindLabel(item.kind)}</TableCell>
        <TableCell data-testid="task-command-state">{commandStateLabel(item.state)}</TableCell>
        <TableCell className="font-mono text-xs">{item.issuer}</TableCell>
        <TableCell>{timeLabel(item.issued_at)}</TableCell>
        <TableCell>
          <Button
            variant="outline"
            size="sm"
            aria-expanded={expanded}
            data-testid="task-command-expand"
            onClick={onToggle}
          >
            {expanded ? "收起" : "明细"}
          </Button>
        </TableCell>
      </TableRow>
      {expanded && (
        <TableRow data-testid="task-command-detail">
          <TableCell colSpan={5} className="bg-muted/40">
            <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-xs">
              <dt className="text-muted-foreground">command_id</dt>
              <dd className="font-mono">{item.command_id}</dd>
              <dt className="text-muted-foreground">Task 引用</dt>
              <dd className="font-mono">{backendRefText(item.task_ref)}</dd>
              <dt className="text-muted-foreground">Run 引用</dt>
              <dd className="font-mono" data-testid="task-command-run-ref">
                {refCell(item.run_ref, "无 Run 引用")}
              </dd>
              <dt className="text-muted-foreground">过期时间 expires_at</dt>
              <dd data-testid="task-command-expires">
                {item.expires_at ? timeLabel(item.expires_at) : "未记录"}
              </dd>
              <dt className="text-muted-foreground">期望版本 expected_revision</dt>
              <dd data-testid="task-command-revision">
                {item.expected_revision ?? "未记录"}
              </dd>
            </dl>
          </TableCell>
        </TableRow>
      )}
    </>
  );
}

export function TaskCommandsPanel({
  taskId,
  pageSize = TASK_COMMANDS_PAGE_SIZE,
  refreshSignal = 0,
}: {
  taskId: string;
  pageSize?: number;
  /** Bumped by siblings (e.g. an issued command) to re-read the current page. */
  refreshSignal?: number;
}) {
  // A keyed content instance owns one Task visit, including pagination and
  // expansion. Returning to the same Task starts a fresh visit at offset zero.
  return (
    <TaskCommandsContent
      key={taskId}
      taskId={taskId}
      pageSize={pageSize}
      refreshSignal={refreshSignal}
    />
  );
}

function TaskCommandsContent({
  taskId,
  pageSize,
  refreshSignal,
}: {
  taskId: string;
  pageSize: number;
  refreshSignal: number;
}) {
  const [pos, setPosition] = useState<Position>(FIRST_POSITION);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [state, setState] = useState<PanelState>({ kind: "loading" });
  const [reloadTick, setReloadTick] = useState(0);
  // Invalidate old page/refresh responses, Strict Mode replays, and requests
  // from an unmounted Task visit, including an A -> B -> A round trip.
  const requestSeq = useRef(0);

  useEffect(() => {
    const seq = ++requestSeq.current;
    if (refreshSignal === 0) setState({ kind: "loading" });
    async function fetchPage() {
      try {
        const page = await taskCommandsApi.list(taskId, pageSize, pos.offset);
        if (seq !== requestSeq.current) return;
        setState({ kind: "ready", page, pos });
      } catch (err) {
        if (seq !== requestSeq.current) return;
        setState({
          kind: "error", pos, code: taskErrorCode(err), message: taskErrorMessage(err),
        });
      }
    }
    if (taskId) void fetchPage();
    return () => { requestSeq.current += 1; };
    // A refreshSignal bump re-reads the current page in place; the initial
    // mount (signal 0) is the only run that shows the loading state.
  }, [taskId, reloadTick, pageSize, pos, refreshSignal]);

  function load(next: Position) {
    // A fresh position object re-runs the fetch effect — including retry,
    // where `next` may be the very object the effect already consumed.
    // Expansion survives page moves: it is keyed by command_id and simply
    // renders nothing when that row is not on the current page.
    setPosition({ offset: next.offset, prevOffsets: [...next.prevOffsets] });
  }

  const ready = state.kind === "ready" ? state : null;
  // Both ready and error states carry the position they were loaded for, so a
  // failed later page still offers the way back to the previous offset.
  const loadedPos = state.kind === "ready" || state.kind === "error" ? state.pos : null;
  // A full page may have a successor; a short page is the last one. `total`
  // stays informational and never decides navigation.
  const hasNext = ready !== null && ready.page.items.length === pageSize;
  const hasPrev = loadedPos !== null && loadedPos.prevOffsets.length > 1;

  return (
    <div className="space-y-2" aria-live="polite" data-testid="task-commands-panel">
      <div className="flex justify-end gap-2">
        <Button variant="outline" size="sm" disabled={!hasPrev || state.kind === "loading"}
          onClick={() => {
            if (!loadedPos) return;
            const prevOffsets = loadedPos.prevOffsets.slice(0, -1);
            load({ offset: prevOffsets[prevOffsets.length - 1], prevOffsets });
          }}>
          上一页
        </Button>
        <Button variant="outline" size="sm" disabled={!hasNext || state.kind === "loading"}
          onClick={() => {
            if (!ready) return;
            const nextOffset = ready.pos.offset + pageSize;
            load({ offset: nextOffset, prevOffsets: [...ready.pos.prevOffsets, nextOffset] });
          }}>
          下一页
        </Button>
        <Button variant="outline" size="sm" disabled={state.kind === "loading"}
          onClick={() => setReloadTick((t) => t + 1)}>
          刷新
        </Button>
      </div>

      {state.kind === "loading" && <p data-testid="task-commands-loading">命令回执加载中…</p>}
      {state.kind === "error" && (
        <div data-testid="task-commands-error" className="space-y-2">
          <p role="alert">命令回执查询失败：{state.code && `[${state.code}] `}{state.message}</p>
          <Button variant="outline" size="sm" onClick={() => load(state.pos)}>
            重试
          </Button>
        </div>
      )}
      {ready && ready.page.items.length === 0 && (
        <p data-testid="task-commands-empty">暂无命令</p>
      )}
      {ready && ready.page.items.length > 0 && (
        <div data-testid="task-commands-table">
          <p className="mb-2 text-sm text-muted-foreground">
            回执状态为服务端已记录事实：“已请求”“已确认接收”不代表命令已生效。
          </p>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>命令</TableHead>
                <TableHead>回执状态</TableHead>
                <TableHead>发起者</TableHead>
                <TableHead>发起时间</TableHead>
                <TableHead>明细</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {ready.page.items.map((item) => (
                <CommandRow
                  key={item.command_id}
                  item={item}
                  expanded={expandedId === item.command_id}
                  onToggle={() =>
                    setExpandedId((current) => (current === item.command_id ? null : item.command_id))}
                />
              ))}
            </TableBody>
          </Table>
          <p className="mt-1 text-xs text-muted-foreground">
            本页 {ready.page.items.length} 条；累计 {ready.page.total} 条
          </p>
        </div>
      )}
      {ready && ready.page.items.length > 0 && !hasNext && (
        <p data-testid="task-commands-end">已是最后一页</p>
      )}
    </div>
  );
}
