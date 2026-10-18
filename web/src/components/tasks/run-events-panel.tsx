"use client";

// Read-only, cursor-paginated event timeline for one selected Run. Query
// results (empty, last page) are never completion facts; gap rows render the
// server-provided missing range instead of normal event fields.

import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import {
  runEventsApi, type RunEventItem, type RunEventsPage,
} from "@/lib/run-event-types";
import { taskErrorCode, taskErrorMessage } from "@/lib/task-types";

type Cursor = string | null;

type PanelState =
  | { kind: "loading" }
  | { kind: "error"; cursor: Cursor; prevCursors: Cursor[]; code: string | null; message: string }
  | { kind: "ready"; page: RunEventsPage; prevCursors: Cursor[] };

const FIRST_PAGE: Cursor = null;

function occurredLabel(occurredAt: string): string {
  const date = new Date(occurredAt);
  return Number.isNaN(date.getTime()) ? occurredAt : date.toLocaleString();
}

function EventRow({ item }: { item: RunEventItem }) {
  if (item.kind === "gap") {
    const range = item.gap;
    return (
      <TableRow data-testid="run-events-gap">
        <TableCell colSpan={6} className="text-amber-600 dark:text-amber-400">
          缺失事件：序号 {range ? range.from_sequence : "?"}–{range ? range.to_sequence : "?"}
        </TableCell>
      </TableRow>
    );
  }
  return (
    <TableRow data-testid="run-events-row">
      <TableCell className="font-mono text-xs">{item.event_id}</TableCell>
      <TableCell>{item.kind}</TableCell>
      <TableCell>{item.source ?? "—"}</TableCell>
      <TableCell className="font-mono">{item.source_sequence}</TableCell>
      <TableCell>{occurredLabel(item.occurred_at)}</TableCell>
      <TableCell className="font-mono text-xs">{item.payload_schema_ref ?? "—"}</TableCell>
    </TableRow>
  );
}

export function RunEventsPanel({ runId }: { runId: string }) {
  const [response, setResponse] = useState<{ runId: string; state: PanelState }>({
    runId, state: { kind: "loading" },
  });
  const [reloadTick, setReloadTick] = useState(0);
  // Monotonic request token: any newer load invalidates every in-flight one,
  // covering same-Run races (fast paging, refresh) that runId matching alone
  // cannot. Strict Mode replays are absorbed the same way.
  const requestSeq = useRef(0);
  const state: PanelState = response.runId === runId ? response.state : { kind: "loading" };

  useEffect(() => {
    const seq = ++requestSeq.current;
    setResponse({ runId, state: { kind: "loading" } });
    async function fetchPage() {
      try {
        const page = await runEventsApi.getEvents(runId, FIRST_PAGE);
        if (seq !== requestSeq.current) return;
        setResponse({
          runId,
          state: { kind: "ready", page, prevCursors: [FIRST_PAGE] },
        });
      } catch (err) {
        if (seq !== requestSeq.current) return;
        setResponse({ runId, state: {
          kind: "error", cursor: FIRST_PAGE, prevCursors: [FIRST_PAGE],
          code: taskErrorCode(err), message: taskErrorMessage(err),
        } });
      }
    }
    if (runId) void fetchPage();
    return () => { requestSeq.current += 1; };
  }, [runId, reloadTick]);

  async function load(cursor: Cursor, prevCursors: Cursor[]) {
    const seq = ++requestSeq.current;
    setResponse({ runId, state: { kind: "loading" } });
    try {
      const page = await runEventsApi.getEvents(runId, cursor);
      if (seq !== requestSeq.current) return;
      setResponse({
        runId,
        // Empty pages still carry navigation facts, including the way back.
        state: { kind: "ready", page, prevCursors },
      });
    } catch (err) {
      if (seq !== requestSeq.current) return;
      setResponse({ runId, state: {
        kind: "error", cursor, prevCursors,
        code: taskErrorCode(err), message: taskErrorMessage(err),
      } });
    }
  }

  const ready = state.kind === "ready" ? state : null;
  const hasPrev = ready !== null && ready.prevCursors.length > 1;
  const nextCursor = ready?.page.next_cursor;
  const hasNext = ready !== null && ready.page.has_more
    && typeof nextCursor === "string" && nextCursor.length > 0;

  return (
    <div className="space-y-2" aria-live="polite" data-testid="run-events-panel">
      <div className="flex justify-end gap-2">
        <Button variant="outline" size="sm" disabled={!hasPrev || state.kind === "loading"}
          onClick={() => {
            if (!ready) return;
            const prevCursors = ready.prevCursors.slice(0, -1);
            void load(prevCursors[prevCursors.length - 1], prevCursors);
          }}>
          上一页
        </Button>
        <Button variant="outline" size="sm" disabled={!hasNext || state.kind === "loading"}
          onClick={() => {
            if (!ready || !hasNext || typeof nextCursor !== "string") return;
            void load(nextCursor, [...ready.prevCursors, nextCursor]);
          }}>
          下一页
        </Button>
        <Button variant="outline" size="sm" disabled={state.kind === "loading"}
          onClick={() => setReloadTick((t) => t + 1)}>
          刷新
        </Button>
      </div>

      {state.kind === "loading" && <p data-testid="run-events-loading">事件加载中…</p>}
      {state.kind === "error" && (
        <div data-testid="run-events-error" className="space-y-2">
          <p role="alert">事件加载失败：{state.code && `[${state.code}] `}{state.message}</p>
          <Button variant="outline" size="sm"
            onClick={() => void load(state.cursor, state.prevCursors)}>
            重试
          </Button>
        </div>
      )}
      {ready && (
        ready.page.events.length === 0 ? (
          <p data-testid="run-events-empty">暂无事件</p>
        ) : (
          <Table data-testid="run-events-table">
            <TableHeader>
              <TableRow>
                <TableHead>事件标识</TableHead>
                <TableHead>类型</TableHead>
                <TableHead>来源</TableHead>
                <TableHead>序号</TableHead>
                <TableHead>发生时间</TableHead>
                <TableHead>Payload Schema</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {ready.page.events.map((item) => (
                <EventRow key={`${item.event_id}:${item.source_sequence}`} item={item} />
              ))}
            </TableBody>
          </Table>
        )
      )}
      {ready && !ready.page.has_more && (
        <p data-testid="run-events-end">已是最后一页</p>
      )}
    </div>
  );
}
