# ADR-010: React Flow Canvas with JSON DSL Bidirectional Sync

> **Status**: Accepted

## Context

The visual canvas is a core deliverable — it allows non-technical users to design agent workflows by dragging and connecting nodes. The design needed to determine the canvas engine, node rendering strategy, and data synchronization approach.

## Decision

Use **React Flow** as the canvas engine. Custom React components render each node type. The **JSON DSL is the single source of truth** — the canvas is a visual editor for the DSL, with bidirectional synchronization.

## Rationale

React Flow is open-source (MIT), actively maintained, and supports custom nodes/edges with Mini Map and Controls out of the box. It integrates naturally with React 19 + TypeScript + Vite.

Bidirectional sync means: canvas operations (drag, connect, delete) update the JSON DSL, which triggers recompilation. Conversely, JSON DSL changes (via code editor or API) update the canvas rendering. This ensures the canvas and DSL never diverge.

Each node type (`llm`, `code`, `condition`, `tool`, `agent`, `subgraph`) has its own visual component reflecting its semantics. Conditional edges display labels (true/false); normal edges use a default style.

## Consequences

- The canvas is always in sync with the DSL — no separate state
- Users can switch between visual and code editing freely
- Custom node components must be maintained for each node type
- The canvas works with the same compiler and engine as code-defined workflows
