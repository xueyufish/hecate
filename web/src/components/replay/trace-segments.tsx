"use client";

import { Badge } from "@/components/ui/badge";
import type { ReplayTimelineResponse } from "@/lib/api-client";

interface Props {
  timeline: ReplayTimelineResponse;
  selectedTraceId: string | null;
  onSelect: (traceId: string | null) => void;
}

export function TraceSegments({ timeline, selectedTraceId, onSelect }: Props) {
  const all = timeline.traces;
  const unattributed = timeline.unattributed;
  const showUnattributed = unattributed.length > 0;

  return (
    <div className="flex flex-wrap gap-2 items-center">
      {all.map((seg) => {
        const active = seg.trace_id === selectedTraceId;
        return (
          <button
            key={seg.trace_id}
            onClick={() => onSelect(seg.trace_id)}
            className={`px-3 py-1 rounded border text-sm ${
              active ? "bg-blue-100 border-blue-400" : "bg-white hover:bg-gray-50"
            }`}
          >
            <span className="font-mono text-xs">{seg.trace_id.slice(0, 8)}</span>
            <Badge className="ml-2">{seg.event_count}</Badge>
          </button>
        );
      })}
      {showUnattributed && (
        <button
          onClick={() => onSelect("__unattributed__")}
          className={`px-3 py-1 rounded border text-sm ${
            selectedTraceId === "__unattributed__"
              ? "bg-yellow-100 border-yellow-400"
              : "bg-white hover:bg-gray-50"
          }`}
        >
          <span>unattributed</span>
          <Badge className="ml-2">{unattributed.length}</Badge>
        </button>
      )}
    </div>
  );
}