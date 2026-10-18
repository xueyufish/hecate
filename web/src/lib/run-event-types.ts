// Read-only fields from the Run event envelope and its cursor query API.

import { api } from "@/lib/api-client";

export type RunEventKind = "event" | "terminal" | "gap";

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
  getEvents(runId: string, cursor?: string | null) {
    const params = new URLSearchParams();
    if (cursor) params.set("cursor", cursor);
    const query = params.toString();
    return api.get<RunEventsPage>(`/api/runs/${runId}/events${query ? `?${query}` : ""}`);
  },
};
