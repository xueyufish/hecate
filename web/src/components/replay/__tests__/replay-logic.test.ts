import { describe, it, expect } from "vitest";
import type { ReplayEvent } from "@/lib/api-client";

// Mirrors the node-aggregation logic in components/replay/dag-replay.tsx
// (extracted for unit testing — no React rendering needed).

interface NodeSpan {
  node_id: string;
  first_seen: number;
  last_seen: number;
}

function aggregateNodes(events: ReplayEvent[]): NodeSpan[] {
  const seen = new Map<string, NodeSpan>();
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
}

function activeNodesAt(events: ReplayEvent[], superstep: number): Set<string> {
  const out = new Set<string>();
  for (const e of events) {
    if (
      e.superstep === superstep &&
      (e.event_type === "NODE_START" || e.event_type === "NODE_END")
    ) {
      if (e.node_id) out.add(e.node_id);
    }
  }
  return out;
}

describe("dag-replay node aggregation", () => {
  it("returns empty array for no events", () => {
    expect(aggregateNodes([])).toEqual([]);
  });

  it("extracts unique nodes in first-seen order", () => {
    const events: ReplayEvent[] = [
      { event_type: "NODE_START", superstep: 1, node_id: "B", timestamp: "t", version: 1, payload: {} },
      { event_type: "NODE_END", superstep: 1, node_id: "B", timestamp: "t", version: 2, payload: {} },
      { event_type: "NODE_START", superstep: 2, node_id: "A", timestamp: "t", version: 3, payload: {} },
    ];
    const nodes = aggregateNodes(events);
    expect(nodes.map((n) => n.node_id)).toEqual(["B", "A"]);
  });

  it("ignores non-node events", () => {
    const events: ReplayEvent[] = [
      { event_type: "CHANNEL_WRITE", superstep: 1, node_id: "ignored", timestamp: "t", version: 1, payload: {} },
      { event_type: "LLM_REQUEST", superstep: 1, node_id: "ignored", timestamp: "t", version: 2, payload: {} },
    ];
    expect(aggregateNodes(events)).toEqual([]);
  });

  it("computes first/last superstep ranges", () => {
    const events: ReplayEvent[] = [
      { event_type: "NODE_START", superstep: 1, node_id: "X", timestamp: "t", version: 1, payload: {} },
      { event_type: "NODE_END", superstep: 3, node_id: "X", timestamp: "t", version: 4, payload: {} },
    ];
    const [x] = aggregateNodes(events);
    expect(x.first_seen).toBe(1);
    expect(x.last_seen).toBe(3);
  });

  it("handles nodes not in current definition (missing from topology)", () => {
    // aggregateNodes returns whatever appears in the log; the UI surfaces
    // them with a label (see dag-replay.tsx message). This test guards the
    // contract that unknown nodes are still rendered.
    const events: ReplayEvent[] = [
      { event_type: "NODE_START", superstep: 1, node_id: "stale_node", timestamp: "t", version: 1, payload: {} },
    ];
    expect(aggregateNodes(events).map((n) => n.node_id)).toEqual(["stale_node"]);
  });
});

describe("dag-replay active superstep highlight", () => {
  it("returns empty set when no superstep matches", () => {
    const events: ReplayEvent[] = [
      { event_type: "NODE_START", superstep: 1, node_id: "A", timestamp: "t", version: 1, payload: {} },
    ];
    expect(activeNodesAt(events, 99).size).toBe(0);
  });

  it("returns nodes active at the selected superstep", () => {
    const events: ReplayEvent[] = [
      { event_type: "NODE_START", superstep: 1, node_id: "A", timestamp: "t", version: 1, payload: {} },
      { event_type: "NODE_END", superstep: 1, node_id: "A", timestamp: "t", version: 2, payload: {} },
      { event_type: "NODE_START", superstep: 2, node_id: "B", timestamp: "t", version: 3, payload: {} },
    ];
    const active = activeNodesAt(events, 1);
    expect([...active]).toEqual(["A"]);
  });
});