"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { replayApi, type ReplayEvent, type ReplayTimelineResponse } from "@/lib/api-client";
import { TraceSegments } from "./trace-segments";
import { Timeline } from "./timeline";
import { EventDetail } from "./event-detail";
import { DagReplay } from "./dag-replay";
import { StateInspector } from "./state-inspector";

interface Props {
  sessionId: string;
}

export function ReplayView({ sessionId }: Props) {
  const router = useRouter();
  const [timeline, setTimeline] = useState<ReplayTimelineResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedTrace, setSelectedTrace] = useState<string | null>(null);
  const [selectedEvent, setSelectedEvent] = useState<ReplayEvent | null>(null);
  const [atVersion, setAtVersion] = useState<number>(0);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    (async () => {
      try {
        const t = await replayApi.getReplayTimeline(sessionId, { limit: 500 });
        if (!cancelled) {
          setTimeline(t);
          setSelectedTrace(t.traces[0]?.trace_id ?? "__unattributed__");
        }
      } catch (e: unknown) {
        const err = e as { error?: { code?: string } };
        if (!cancelled) setError(err?.error?.code ?? "load_failed");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  const visibleEvents: ReplayEvent[] = useMemo(() => {
    if (!timeline) return [];
    if (selectedTrace === "__unattributed__") return timeline.unattributed;
    return (
      timeline.traces.find((s) => s.trace_id === selectedTrace)?.events ?? []
    );
  }, [timeline, selectedTrace]);

  const maxVersion = useMemo(() => {
    if (!timeline) return 0;
    let m = 0;
    for (const s of timeline.traces) {
      for (const e of s.events) if (e.version > m) m = e.version;
    }
    for (const e of timeline.unattributed) if (e.version > m) m = e.version;
    return m;
  }, [timeline]);

  if (error) return <div className="text-red-600 text-sm">{error}</div>;
  if (!timeline) return <div className="text-gray-500">Loading replay…</div>;

  return (
    <div className="space-y-4">
      <div className="rounded border bg-yellow-50 border-yellow-300 px-3 py-2 text-xs text-yellow-800">
        Replay covers Pregel-path execution only. Path A / path C calls are not in the event log.
      </div>

      <TraceSegments
        timeline={timeline}
        selectedTraceId={selectedTrace}
        onSelect={setSelectedTrace}
      />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2 space-y-4">
          <Timeline
            events={visibleEvents}
            guards={timeline.guardrail_blocks}
            onSelectEvent={setSelectedEvent}
            selectedVersion={selectedEvent?.version ?? null}
          />
          <DagReplay
            events={visibleEvents}
            selectedSuperstep={selectedEvent?.superstep ?? null}
            onSubgraphClick={(child) => router.push(`/ops-center/conversations/${child}`)}
          />
        </div>
        <div className="space-y-4">
          <EventDetail event={selectedEvent} />
          <div>
            <label className="block text-xs text-gray-500 mb-1">
              time-travel version (0..{maxVersion})
            </label>
            <input
              type="range"
              min={0}
              max={maxVersion}
              value={atVersion}
              onChange={(e) => setAtVersion(parseInt(e.target.value, 10))}
              className="w-full"
            />
            <div className="text-xs text-gray-500 mt-1">at_version = {atVersion}</div>
          </div>
          <StateInspector sessionId={sessionId} atVersion={atVersion} />
        </div>
      </div>
    </div>
  );
}