import { describe, expect, it } from "vitest";
import { dslToReactFlow, reactFlowToDsl } from "../dsl-bridge";
import type { FlowNode } from "../dsl-bridge";

/** Helper: minimal valid DSL for testing */
function makeDsl(overrides: Record<string, unknown> = {}) {
  return {
    name: "test-workflow",
    nodes: {
      start: { type: "conversation" as const, config: { system_prompt: "Hello" } },
      end: { type: "conversation" as const, config: {} },
    },
    edges: [{ source: "start", target: "end" }],
    entry: "start",
    ...overrides,
  };
}

describe("dslToReactFlow", () => {
  it("converts simple DSL to React Flow nodes and edges", () => {
    const dsl = makeDsl();
    const { nodes, edges } = dslToReactFlow(dsl);

    expect(nodes).toHaveLength(2);
    expect(nodes[0].id).toBe("start");
    expect(nodes[0].type).toBe("conversation");
    expect(nodes[1].id).toBe("end");

    expect(edges).toHaveLength(1);
    expect(edges[0].source).toBe("start");
    expect(edges[0].target).toBe("end");
  });

  it("assigns grid positions based on node order", () => {
    const dsl = makeDsl();
    const { nodes } = dslToReactFlow(dsl);

    // First node: index 0 → x=0, y=0
    expect(nodes[0].position).toEqual({ x: 0, y: 0 });
    // Second node: index 1 → x=250, y=0
    expect(nodes[1].position).toEqual({ x: 250, y: 0 });
  });

  it("uses system_prompt as node label when present", () => {
    const dsl = makeDsl();
    const { nodes } = dslToReactFlow(dsl);

    const startNode = nodes.find((n) => n.id === "start");
    expect(startNode?.data.label).toBe("Hello");
  });

  it("handles conditional edges by expanding dict targets", () => {
    const dsl = makeDsl({
      nodes: {
        check: { type: "condition" as const, config: { expression: "x > 0" } },
        yes: { type: "conversation" as const, config: {} },
        no: { type: "conversation" as const, config: {} },
      },
      edges: [{ source: "check", target: { true: "yes", false: "no" } }],
      entry: "check",
    });

    const { edges } = dslToReactFlow(dsl);
    expect(edges).toHaveLength(2);

    const trueEdge = edges.find((e) => e.label === "true");
    const falseEdge = edges.find((e) => e.label === "false");
    expect(trueEdge).toBeDefined();
    expect(falseEdge).toBeDefined();
    expect(trueEdge!.target).toBe("yes");
    expect(falseEdge!.target).toBe("no");
  });

  it("renders handoff edges with dashed purple style", () => {
    const dsl = makeDsl({
      edges: [{ source: "start", target: "end", trigger: "handoff" }],
    });

    const { edges } = dslToReactFlow(dsl);
    expect(edges).toHaveLength(1);
    expect(edges[0].style).toEqual({
      stroke: "#8b5cf6",
      strokeWidth: 2,
      strokeDasharray: "5 5",
    });
    expect(edges[0].label).toBe("Handoff");
    expect((edges[0].data as Record<string, unknown>)?.edgeType).toBe("handoff");
  });

  it("detects handoff via type field", () => {
    const dsl = makeDsl({
      edges: [{ source: "start", target: "end", type: "handoff" }],
    });

    const { edges } = dslToReactFlow(dsl);
    expect((edges[0].data as Record<string, unknown>)?.edgeType).toBe("handoff");
  });

  it("uses NODE_TYPE_LABELS as fallback label", () => {
    const dsl = makeDsl({
      nodes: {
        tool: { type: "tool-call" as const, config: {} },
      },
      edges: [],
      entry: "tool",
    });

    const { nodes } = dslToReactFlow(dsl);
    expect(nodes[0].data.label).toBe("Tool Call");
  });
});

describe("reactFlowToDsl", () => {
  it("converts React Flow nodes and edges back to DSL format", () => {
    const nodes = [
      { id: "a", type: "conversation", position: { x: 0, y: 0 }, data: { label: "A", type: "conversation", config: {} } },
      { id: "b", type: "conversation", position: { x: 250, y: 0 }, data: { label: "B", type: "conversation", config: { system_prompt: "Hi" } } },
    ];
    const edges = [
      { id: "e-0", source: "a", target: "b" },
    ];

    const dsl = reactFlowToDsl(nodes as FlowNode[], edges, "test");

    expect(dsl.version).toBe("1.0");
    expect(dsl.name).toBe("test");
    expect(dsl.nodes.a).toEqual({ type: "conversation", config: {} });
    expect(dsl.nodes.b).toEqual({ type: "conversation", config: { system_prompt: "Hi" } });
    expect(dsl.edges).toHaveLength(1);
    expect(dsl.edges[0].source).toBe("a");
    expect(dsl.edges[0].target).toBe("b");
  });

  it("merges conditional edges back into dict targets", () => {
    const nodes = [
      { id: "check", type: "condition", position: { x: 0, y: 0 }, data: { label: "Check", type: "condition", config: {} } },
      { id: "yes", type: "conversation", position: { x: 250, y: 0 }, data: { label: "Yes", type: "conversation", config: {} } },
      { id: "no", type: "conversation", position: { x: 0, y: 150 }, data: { label: "No", type: "conversation", config: {} } },
    ];
    const edges = [
      { id: "e-0", source: "check", target: "yes", label: "true" },
      { id: "e-1", source: "check", target: "no", label: "false" },
    ];

    const dsl = reactFlowToDsl(nodes as FlowNode[], edges, "cond");

    expect(dsl.edges).toHaveLength(1);
    expect(dsl.edges[0].source).toBe("check");
    expect(dsl.edges[0].target).toEqual({ true: "yes", false: "no" });
  });

  it("preserves handoff edges", () => {
    const nodes = [
      { id: "a", type: "conversation", position: { x: 0, y: 0 }, data: { label: "A", type: "conversation", config: {} } },
      { id: "b", type: "agent", position: { x: 250, y: 0 }, data: { label: "B", type: "agent", config: {} } },
    ];
    const edges = [
      { id: "e-0", source: "a", target: "b", data: { edgeType: "handoff" } },
    ];

    const dsl = reactFlowToDsl(nodes as FlowNode[], edges, "handoff");

    expect(dsl.edges).toHaveLength(1);
    expect(dsl.edges[0].type).toBe("handoff");
    expect(dsl.edges[0].trigger).toBe("handoff");
  });

  it("sets entry to first node id", () => {
    const nodes = [
      { id: "first", type: "conversation", position: { x: 0, y: 0 }, data: { label: "F", type: "conversation", config: {} } },
    ];
    const edges: unknown[] = [];

    const dsl = reactFlowToDsl(nodes as FlowNode[], edges as never[], "single");

    expect(dsl.entry).toBe("first");
  });
});

describe("round-trip: DSL → ReactFlow → DSL", () => {
  it("preserves simple nodes and edges through round-trip", () => {
    const original = makeDsl();
    const { nodes, edges } = dslToReactFlow(original);
    const restored = reactFlowToDsl(nodes, edges, "test-workflow");

    // Nodes should be equivalent
    for (const id of Object.keys(original.nodes)) {
      expect(restored.nodes[id]).toEqual(original.nodes[id]);
    }

    // Edges should be equivalent (same source/target pairs)
    expect(restored.edges).toHaveLength(original.edges.length);
    expect(restored.edges[0].source).toBe(original.edges[0].source);
    expect(restored.edges[0].target).toBe(original.edges[0].target);
  });

  it("preserves conditional edges through round-trip", () => {
    const original = makeDsl({
      nodes: {
        check: { type: "condition" as const, config: { expression: "x > 0" } },
        pass: { type: "conversation" as const, config: {} },
        fail: { type: "conversation" as const, config: {} },
      },
      edges: [{ source: "check", target: { true: "pass", false: "fail" } }],
      entry: "check",
    });

    const { nodes, edges } = dslToReactFlow(original);
    const restored = reactFlowToDsl(nodes, edges, "test-workflow");

    expect(restored.edges).toHaveLength(1);
    expect(restored.edges[0].source).toBe("check");
    expect(restored.edges[0].target).toEqual({ true: "pass", false: "fail" });
  });

  it("preserves handoff edges through round-trip", () => {
    const original = makeDsl({
      edges: [{ source: "start", target: "end", trigger: "handoff" }],
    });

    const { nodes, edges } = dslToReactFlow(original);
    const restored = reactFlowToDsl(nodes, edges, "test-workflow");

    expect(restored.edges).toHaveLength(1);
    expect(restored.edges[0].source).toBe("start");
    expect(restored.edges[0].target).toBe("end");
    expect(restored.edges[0].type).toBe("handoff");
    expect(restored.edges[0].trigger).toBe("handoff");
  });
});
