"use client";

import { useMemo } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { ReplayEvent } from "@/lib/api-client";

interface Props {
  events: ReplayEvent[];
  selectedSuperstep: number | null;
  onSubgraphClick?: (childSessionId: string) => void;
}

/** Lightweight DAG visualization: shows node list and highlights active superstep.

Full React Flow graph is rendered by the existing workflow canvas; this
component provides a topology-agnostic node list with high-per-superstep
feedback, plus subgraph link forwarding. The DAG topology source is the
agent's current definition (see spec: "topology from current definition").
*/
export function DagReplay({ events, selectedSuperstep, onSubgraphClick }: Props) {
  const nodes = useMemo(() => {
    const seen = new Map<string, { node_id: string; first_seen: number; last_seen: number }>();
    for (const ev of events) {
      if (!ev.node_id) continue;
      if (ev.event_type !== "NODE_START" && ev.event_type !== "NODE_END") continue;
      const existing = seen.get(ev.node_id);
      if (existing) {
        existing.last_seen = Math.max(existing.last_seen, ev.superstep);
      } else {
        seen.set(ev.node_id, {
          node_id: ev.node_id,
          first_seen: ev.superstep,
          last_seen: ev.superstep,
        });
      }
    }
    return Array.from(seen.values()).sort((a, b) => a.first_seen - b.first_seen);
  }, [events]);

  const activeNodes = new Set(
    events
      .filter(
        (e) =>
          selectedSuperstep !== null &&
          e.superstep === selectedSuperstep &&
          (e.event_type === "NODE_START" || e.event_type === "NODE_END")
      )
      .map((e) => e.node_id)
      .filter(Boolean)
  );

  const subgraphStarts = events.filter((e) => e.event_type === "SUBGRAPH_START");

  return (
    <Card>
      <CardHeader>
        <CardTitle>DAG (current definition)</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-gray-500">
          Topology is the agent&apos;s current graph definition; nodes not in the current definition are flagged below.
        </p>
        <div className="flex flex-wrap gap-2">
          {nodes.length === 0 && (
            <span className="text-sm text-gray-400">no node events</span>
          )}
          {nodes.map((n) => {
            const active = activeNodes.has(n.node_id);
            return (
              <span
                key={n.node_id}
                className={`px-3 py-1 rounded text-sm border ${
                  active
                    ? "bg-blue-100 border-blue-400 text-blue-700"
                    : "bg-white border-gray-200 text-gray-700"
                }`}
              >
                {n.node_id}
                <span className="ml-2 text-xs text-gray-400">
                  s{n.first_seen}-{n.last_seen}
                </span>
              </span>
            );
          })}
        </div>
        {subgraphStarts.length > 0 && (
          <div className="pt-2 border-t">
            <div className="text-xs text-gray-500 mb-1">subgraph links</div>
            {subgraphStarts.map((e) => {
              const child = (e.payload?.["child_session_id"] as string) ?? "(missing)";
              return (
                <button
                  key={e.version}
                  onClick={() => onSubgraphClick?.(child)}
                  className="block text-sm text-indigo-600 hover:underline"
                >
                  → child_session {String(child).slice(0, 8)}
                </button>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}