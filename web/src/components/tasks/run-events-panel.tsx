"use client";

// Read-only event timeline for one selected Run. Two independent views:
// cursor-paginated history (query facts only) and an optional live SSE
// observation. Live observation follows the server's facts — reconnects
// resume from the last ACCEPTED envelope's source_sequence, duplicate
// deliveries dedupe by event id, gaps render only as server-provided gap
// rows, and the observation ending on a terminal event is a stream fact,
// never a claim that execution succeeded. Starting, pausing or leaving
// the observation only opens or closes the connection; it never issues
// execution control commands.

import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import {
  isRunTerminalEvent,
  runEventsApi,
  RunStreamDataError,
  type RunEventItem,
  type RunEventsPage,
} from "@/lib/run-event-types";
import { taskErrorCode, taskErrorMessage } from "@/lib/task-types";

type Cursor = string | null;

type PanelState =
  | { kind: "loading" }
  | { kind: "error"; cursor: Cursor; prevCursors: Cursor[]; code: string | null; message: string }
  | { kind: "ready"; page: RunEventsPage; prevCursors: Cursor[] };

type LiveStatus =
  | { kind: "idle" }
  | { kind: "connecting" }
  | { kind: "live" }
  | { kind: "reconnecting"; attempt: number }
  /** The server delivered its terminal event: a stream fact, not success. */
  | { kind: "ended"; via: "event" | "query" }
  /** The reconnect budget ran out without observing a terminal event. */
  | { kind: "stopped-no-terminal" }
  | { kind: "error"; code: string | number | null; message: string };

interface LiveState {
  runId: string;
  session: number;
  status: LiveStatus;
  items: RunEventItem[];
  trimmed: boolean;
}

const FIRST_PAGE: Cursor = null;
const LIVE_BUFFER_MAX = 200;
const MAX_RECONNECTS = 6;

function backoffDelay(attempt: number): number {
  return Math.min(500 * 2 ** (attempt - 1), 8000);
}

function occurredLabel(occurredAt: string): string {
  const date = new Date(occurredAt);
  return Number.isNaN(date.getTime()) ? occurredAt : date.toLocaleString();
}

function apiErrorOf(value: unknown): { code: string | null; message: string } | null {
  if (typeof value !== "object" || value === null) return null;
  const raw = value as Record<string, unknown>;
  const detail = typeof raw.detail === "object" && raw.detail !== null
    ? raw.detail as Record<string, unknown> : raw;
  if (typeof detail.error !== "object" || detail.error === null) return null;
  const error = detail.error as Record<string, unknown>;
  return {
    code: typeof error.code === "string" ? error.code : null,
    message: typeof error.message === "string" ? error.message : "事件流连接被拒绝",
  };
}

function liveStatusLabel(status: LiveStatus): string {
  switch (status.kind) {
    case "idle":
      return "未开启";
    case "connecting":
      return "连接中…";
    case "live":
      return "已连接，实时观察中";
    case "reconnecting":
      return `连接中断，正在重连（第 ${status.attempt} 次）…`;
    case "ended":
      return status.via === "event"
        ? "观察结束：已收到终态事件（执行结果以任务详情为准）"
        : "观察结束：Run 查询已确认终态（执行结果以任务详情为准）";
    case "stopped-no-terminal":
      return "连接多次中断且未续接到终态事件，观察已停止；执行状态以任务详情为准";
    case "error":
      return "观察已终止";
  }
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
      <TableCell className="font-mono">{item.source ?? "—"}</TableCell>
      <TableCell className="font-mono">{item.source_sequence}</TableCell>
      <TableCell>{occurredLabel(item.occurred_at)}</TableCell>
      <TableCell className="font-mono text-xs">{item.payload_schema_ref ?? "—"}</TableCell>
    </TableRow>
  );
}

const IDLE_LIVE: Omit<LiveState, "runId"> = {
  session: 0,
  status: { kind: "idle" },
  items: [],
  trimmed: false,
};

export function RunEventsPanel({ runId, onTerminal }: { runId: string; onTerminal?: () => void }) {
  // Each Run visit owns its subscription. Returning to an earlier Run
  // starts idle instead of resurrecting the abandoned session and cursor.
  return <RunEventsContent key={runId} runId={runId} onTerminal={onTerminal} />;
}

function RunEventsContent({ runId, onTerminal }: { runId: string; onTerminal?: () => void }) {
  const onTerminalRef = useRef(onTerminal);
  onTerminalRef.current = onTerminal;
  const [response, setResponse] = useState<{ runId: string; state: PanelState }>({
    runId, state: { kind: "loading" },
  });
  const [reloadTick, setReloadTick] = useState(0);
  // Monotonic request token: any newer load invalidates every in-flight one,
  // covering same-Run races (fast paging, refresh) that runId matching alone
  // cannot. Strict Mode replays are absorbed the same way.
  const requestSeq = useRef(0);
  const state: PanelState = response.runId === runId ? response.state : { kind: "loading" };

  const [live, setLive] = useState<LiveState>({ runId, ...IDLE_LIVE });
  const liveSession = useRef(0);
  // Resume position of the live observation: the source_sequence string of
  // the last accepted envelope. Anchored at session start, advanced only by
  // accepted envelopes — never by a partial or malformed frame.
  const liveCursor = useRef<Cursor>(null);
  const currentLive: LiveState = live.runId === runId ? live : { runId, ...IDLE_LIVE };
  const liveSessionId = currentLive.session;

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

  function startObservation() {
    // Anchor after the page currently in view: the resume position the
    // server itself handed back for this window.
    const tail = state.kind === "ready" ? state.page.next_cursor : null;
    liveCursor.current = tail;
    liveSession.current += 1;
    setLive({ runId, session: liveSession.current, status: { kind: "connecting" }, items: [], trimmed: false });
  }

  function stopObservation() {
    // Closing the observation only drops the connection: no control
    // command is issued and the executing run is never touched. Zeroing
    // the session re-runs the effect, whose cleanup aborts the stream.
    setLive((prev) => (prev.runId === runId ? { ...prev, session: 0, status: { kind: "idle" } } : prev));
  }

  useEffect(() => {
    if (liveSessionId === 0) return;
    const session = liveSessionId;
    const controller = new AbortController();
    let stopped = false;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let failures = 0;
    const acceptedIds = new Set<string>();

    function patch(status: LiveStatus) {
      if (stopped) return;
      setLive((prev) => (prev.runId === runId && prev.session === session ? { ...prev, status } : prev));
    }

    function acceptItem(item: RunEventItem) {
      if (stopped) return;
      setLive((prev) => {
        if (prev.runId !== runId || prev.session !== session) return prev;
        // Duplicate deliveries dedupe by event id; order follows the
        // server's sequence, gaps stay server-provided facts.
        if (prev.items.some((existing) => existing.event_id === item.event_id)) return prev;
        const items = [...prev.items, item].sort((a, b) => a.source_sequence - b.source_sequence);
        const trimmed = prev.trimmed || items.length > LIVE_BUFFER_MAX;
        return { ...prev, items: items.slice(Math.max(0, items.length - LIVE_BUFFER_MAX)), trimmed };
      });
    }

    function scheduleReconnect() {
      failures += 1;
      if (failures > MAX_RECONNECTS) {
        patch({ kind: "stopped-no-terminal" });
        return;
      }
      patch({ kind: "reconnecting", attempt: failures });
      reconnectTimer = setTimeout(() => void run(), backoffDelay(failures));
    }

    async function run() {
      patch(failures === 0 ? { kind: "connecting" } : { kind: "reconnecting", attempt: failures });
      try {
        for await (const message of runEventsApi.streamEvents(
          runId, liveCursor.current, controller.signal, () => patch({ kind: "live" }),
        )) {
          if (stopped) return;
          if (message.kind === "server-error") {
            // The server explicitly ended the observation (e.g. access
            // revoked mid-stream): show the code, never retry.
            patch({ kind: "error", code: message.code, message: `服务端终止了事件流（HTTP ${message.code}）` });
            return;
          }
          const item = message.item;
          // Replayed positions never regress the cursor or replenish the
          // retry budget. Only newly accepted facts establish progress.
          if (liveCursor.current !== null && item.source_sequence <= Number(liveCursor.current)) continue;
          if (acceptedIds.has(item.event_id)) {
            throw new RunStreamDataError("事件流重复使用了事件标识，观察已停止。");
          }
          acceptedIds.add(item.event_id);
          if (acceptedIds.size > LIVE_BUFFER_MAX) {
            const oldest = acceptedIds.values().next().value;
            if (oldest !== undefined) acceptedIds.delete(oldest);
          }
          failures = 0;
          // Accepted: only now may the resume cursor move.
          liveCursor.current = String(item.source_sequence);
          acceptItem(item);
          patch({ kind: "live" });
          if (isRunTerminalEvent(item)) {
            patch({ kind: "ended", via: "event" });
            onTerminalRef.current?.();
            return;
          }
        }
        if (stopped) return;
        // The cursor may already be beyond the terminal event. Confirm
        // that case from the authorized Run query instead of retrying a
        // correctly closed stream or inferring success from EOF.
        const runDetail = await runEventsApi.getRun(runId, controller.signal);
        if (stopped) return;
        if (["succeeded", "failed", "cancelled"].includes(String(runDetail.projection?.state))) {
          patch({ kind: "ended", via: "query" });
          onTerminalRef.current?.();
          return;
        }
        scheduleReconnect();
      } catch (err) {
        if (stopped) return;
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (err instanceof RunStreamDataError) {
          // Malformed data was never accepted and the cursor never moved.
          patch({ kind: "error", code: null, message: err.message });
          return;
        }
        const apiError = apiErrorOf(err);
        if (apiError !== null) {
          // A pre-connection HTTP rejection (visibility, role, …): terminal.
          patch({
            kind: "error",
            code: apiError.code,
            message: apiError.message,
          });
          return;
        }
        scheduleReconnect();
      }
    }

    void run();
    return () => {
      stopped = true;
      controller.abort();
      if (reconnectTimer !== null) clearTimeout(reconnectTimer);
    };
  }, [runId, liveSessionId]);

  const ready = state.kind === "ready" ? state : null;
  const hasPrev = ready !== null && ready.prevCursors.length > 1;
  const nextCursor = ready?.page.next_cursor;
  const hasNext = ready !== null && ready.page.has_more
    && typeof nextCursor === "string" && nextCursor.length > 0;

  const observing = !["idle", "ended", "stopped-no-terminal", "error"].includes(currentLive.status.kind);

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

      <div className="space-y-2 border-t pt-2" data-testid="run-events-live">
        <div className="flex items-center justify-between gap-2">
          <p className="text-sm font-medium" data-testid="live-status">
            实时观察：{liveStatusLabel(currentLive.status)}
          </p>
          {observing ? (
            <Button variant="outline" size="sm" data-testid="live-stop"
              onClick={stopObservation}>
              停止观察
            </Button>
          ) : (
            <Button variant="outline" size="sm" data-testid="live-start"
              onClick={startObservation}>
              开启观察
            </Button>
          )}
        </div>
        <p className="text-xs text-muted-foreground">
          观察从当前浏览位置之后开始；断线后自动从已接收位置续接。开启或停止观察只打开、关闭连接，不会向执行发送任何命令。
        </p>
        {currentLive.items.length > 0 && (
          <div data-testid="live-events">
            {currentLive.trimmed && (
              <p className="text-xs text-muted-foreground" data-testid="live-buffer-trimmed">
                实时缓冲已达上限（保留最新 {LIVE_BUFFER_MAX} 条），更早的事件请使用上方历史查询。
              </p>
            )}
            <Table data-testid="live-events-table">
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
                {currentLive.items.map((item) => (
                  <EventRow key={`${item.event_id}:${item.source_sequence}`} item={item} />
                ))}
              </TableBody>
            </Table>
          </div>
        )}
        {currentLive.status.kind === "error" && (
          <p role="alert" className="text-sm text-destructive" data-testid="live-error">
            {currentLive.status.code !== null && `[${currentLive.status.code}] `}
            {currentLive.status.message}
          </p>
        )}
        {currentLive.status.kind === "stopped-no-terminal" && (
          <Button variant="outline" size="sm" data-testid="live-requery"
            onClick={() => setReloadTick((t) => t + 1)}>
            刷新历史事件
          </Button>
        )}
      </div>
    </div>
  );
}
