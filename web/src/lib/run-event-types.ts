// Read-only fields from the Run event envelope, its cursor query API, and
// the live SSE subscription over the same envelopes.

import { api, type StreamedSseFrame } from "@/lib/api-client";
import type { RunSummary } from "@/lib/task-types";

export type RunEventKind = "event" | "terminal" | "gap";

/** Platform terminal marker schema (the durable run_terminal projection). */
export const RUN_TERMINAL_SCHEMA = "hecate.platform.run_terminal/0";

/** True when the server marked this envelope as the run's terminal fact. */
export function isRunTerminalEvent(item: RunEventItem): boolean {
  return item.kind === "terminal" || item.payload_schema_ref === RUN_TERMINAL_SCHEMA;
}

/** One explicitly missing sequence range; never silently skipped. */
export interface RunEventGapRange {
  from_sequence: number;
  to_sequence: number;
}

/**
 * One server-provided event page entry (EventEnvelope.to_dict() subset).
 * Only observation fields are typed here; payload/extra stay unrendered.
 */
interface RunEventBase {
  event_id: string;
  source_sequence: number;
  occurred_at: string;
  received_at: string;
  source?: string | null;
}

export type RunEventItem = RunEventBase & (
  | { kind: "event" | "terminal"; payload_schema_ref: string; gap?: never }
  | { kind: "gap"; gap: RunEventGapRange; payload_schema_ref?: string | null }
);

export interface RunEventsPage {
  events: RunEventItem[];
  next_cursor: string | null;
  has_more: boolean;
}

export const runEventsApi = {
  getRun(runId: string, signal: AbortSignal) {
    return api.get<RunSummary>(`/api/runs/${runId}`, { signal });
  },
  getEvents(runId: string, cursor?: string | null) {
    const params = new URLSearchParams();
    if (cursor) params.set("cursor", cursor);
    const query = params.toString();
    return api.get<RunEventsPage>(`/api/runs/${runId}/events${query ? `?${query}` : ""}`);
  },

  /**
   * Live SSE subscription starting after ``cursor`` — the source_sequence
   * of the last accepted envelope, or null to start from the beginning.
   * Cancelling ``signal`` stops the underlying request.
   */
  streamEvents(
    runId: string, cursor: string | null, signal: AbortSignal, onOpen?: () => void,
  ): AsyncGenerator<RunStreamMessage> {
    const params = new URLSearchParams();
    if (cursor) params.set("cursor", cursor);
    const query = params.toString();
    return mapRunStream(
      api.getEventStream(`/api/runs/${runId}/events/stream${query ? `?${query}` : ""}`, { signal, onOpen }),
    );
  },
};

// ---------------------------------------------------------------------------
// Live subscription: frames → validated envelopes. The resume cursor may
// only advance over envelopes that passed shape validation; malformed data
// is an error the caller must surface, never a silent skip.
// ---------------------------------------------------------------------------

/** One message off the live stream. */
export type RunStreamMessage =
  | { kind: "envelope"; item: RunEventItem }
  /** The server's explicit ``event: error`` frame (revocation/failure). */
  | { kind: "server-error"; code: number };

/** A frame the consumer cannot interpret; observation must stop on it. */
export class RunStreamDataError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "RunStreamDataError";
  }
}

async function* mapRunStream(frames: AsyncGenerator<StreamedSseFrame>): AsyncGenerator<RunStreamMessage> {
  for await (const frame of frames) {
    if (!frame.terminated) {
      // The connection died mid-frame: nothing was delivered, so nothing
      // is accepted and no cursor moves. Treat as end-of-stream; the
      // caller's reconnect logic resumes from the last accepted position.
      return;
    }
    let payload: unknown;
    try {
      payload = JSON.parse(frame.data);
    } catch {
      throw new RunStreamDataError("事件流返回了无法解析的数据，观察已停止。");
    }
    if (frame.event === "error") {
      const code = streamErrorCode(payload);
      if (code !== null) {
        yield { kind: "server-error", code };
        return;
      }
      throw new RunStreamDataError("事件流返回了无法解释的错误帧，观察已停止。");
    }
    const item = asRunEventItem(payload);
    if (item === null) {
      throw new RunStreamDataError("事件流返回了不符合事件结构的数据，观察已停止。");
    }
    yield { kind: "envelope", item };
  }
}

function streamErrorCode(payload: unknown): number | null {
  if (typeof payload !== "object" || payload === null) return null;
  const code = (payload as { code?: unknown }).code;
  return typeof code === "number" && Number.isInteger(code) ? code : null;
}

/**
 * Shape validation for one streamed envelope: only observation fields are
 * kept (the display whitelist); ``null`` means the envelope is malformed
 * and must never reach the view or move the resume cursor.
 */
export function asRunEventItem(payload: unknown): RunEventItem | null {
  if (typeof payload !== "object" || payload === null) return null;
  const raw = payload as Record<string, unknown>;
  if (typeof raw.event_id !== "string" || !raw.event_id) return null;
  if (!Number.isSafeInteger(raw.source_sequence) || (raw.source_sequence as number) < 0) return null;
  if (typeof raw.occurred_at !== "string" || typeof raw.received_at !== "string") return null;
  const kind = raw.kind;
  if (kind === "gap") {
    const gap = raw.gap;
    if (
      typeof gap !== "object" ||
      gap === null ||
      !Number.isSafeInteger((gap as Record<string, unknown>).from_sequence) ||
      !Number.isSafeInteger((gap as Record<string, unknown>).to_sequence)
    ) {
      return null;
    }
    const range = gap as Record<string, number>;
    if (range.from_sequence < 0 || range.to_sequence < range.from_sequence) return null;
    return {
      event_id: raw.event_id,
      kind: "gap",
      source_sequence: raw.source_sequence as number,
      occurred_at: raw.occurred_at,
      received_at: raw.received_at,
      source: optionalSource(raw.source),
      gap: { from_sequence: range.from_sequence, to_sequence: range.to_sequence },
    };
  }
  if (kind !== "event" && kind !== "terminal") return null;
  if (typeof raw.payload_schema_ref !== "string" || !raw.payload_schema_ref) return null;
  return {
    event_id: raw.event_id,
    kind,
    source_sequence: raw.source_sequence as number,
    occurred_at: raw.occurred_at,
    received_at: raw.received_at,
    source: optionalSource(raw.source),
    payload_schema_ref: raw.payload_schema_ref,
  };
}

function optionalSource(source: unknown): string | null | undefined {
  return typeof source === "string" ? source : null;
}
