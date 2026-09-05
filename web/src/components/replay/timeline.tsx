"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { ReplayEvent, ReplayGuardrailBlock } from "@/lib/api-client";

interface Props {
  events: ReplayEvent[];
  guards: ReplayGuardrailBlock[];
  onSelectEvent: (event: ReplayEvent) => void;
  selectedVersion: number | null;
}

const EVENT_COLORS: Record<string, string> = {
  NODE_START: "bg-blue-100 text-blue-700",
  NODE_END: "bg-blue-50 text-blue-600",
  CHANNEL_WRITE: "bg-purple-100 text-purple-700",
  CHANNEL_WRITE_REJECTED: "bg-red-50 text-red-500",
  LLM_REQUEST: "bg-amber-100 text-amber-700",
  LLM_RESPONSE: "bg-amber-50 text-amber-600",
  TOOL_CALL: "bg-emerald-100 text-emerald-700",
  TOOL_RESULT: "bg-emerald-50 text-emerald-600",
  STEP_END: "bg-gray-200 text-gray-700",
  ERROR: "bg-red-100 text-red-700",
  INTERRUPT: "bg-orange-100 text-orange-700",
  RESUME: "bg-teal-100 text-teal-700",
  SUBGRAPH_START: "bg-indigo-100 text-indigo-700",
  SUBGRAPH_END: "bg-indigo-50 text-indigo-600",
};

export function Timeline({ events, guards, onSelectEvent, selectedVersion }: Props) {
  const guardByVersion = new Map<number, ReplayGuardrailBlock>();
  for (const g of guards) guardByVersion.set(g.version, g);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Timeline</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="space-y-1">
          {events.map((ev) => {
            const cls = EVENT_COLORS[ev.event_type] ?? "bg-gray-100 text-gray-600";
            const active = ev.version === selectedVersion;
            const guard = guardByVersion.get(ev.version);
            return (
              <button
                key={`${ev.version}-${ev.event_type}`}
                onClick={() => onSelectEvent(ev)}
                className={`w-full text-left px-3 py-2 rounded flex items-center gap-3 ${
                  active ? "ring-2 ring-blue-400" : "hover:bg-gray-50"
                }`}
              >
                <span className={`text-xs px-2 py-0.5 rounded ${cls}`}>{ev.event_type}</span>
                <span className="text-xs text-gray-500">v{ev.version}</span>
                <span className="text-xs text-gray-500">s{ev.superstep}</span>
                {ev.node_id && <span className="text-xs text-gray-700">{ev.node_id}</span>}
                {guard && (
                  <span className="text-xs px-2 py-0.5 rounded bg-red-100 text-red-700 ml-auto">
                    guardrail: {guard.reason}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}