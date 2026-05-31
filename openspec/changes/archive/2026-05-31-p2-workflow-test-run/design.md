## Context

The workflow editor at `/workflows/[id]` has:
- Canvas editor with React Flow
- Save button (auto-save with debounce)
- Test Run button calling `POST /api/workflows/{id}/test-run`
- Basic result display showing per-node status (completed/failed)

The backend `WorkflowTestRunner` already:
- Compiles the workflow graph
- Executes nodes via PregelRuntime
- Returns per-node status, output, error_message, duration_ms

What's missing:
- Input customization (currently hardcoded to `{messages: [{role: "user", content: "test"}]}`)
- Node output display (output data is returned but not shown in UI)
- Execution logs (no logging during test runs)
- Visual feedback on canvas (nodes don't change color during execution)
- Run history (no persistence of test runs)

## Goals / Non-Goals

**Goals:**
- Custom input form for test runs
- Node output panel (click node → see input/output)
- Execution logs panel (per-node logs with timestamps)
- Node status badges on canvas (pending/running/completed/failed)
- Run history list (last 10 runs with timestamps and status)

**Non-Goals:**
- Step-through debugging (pause/resume between nodes) — complex, deferred
- Breakpoint support — deferred
- Real-time streaming of execution progress — deferred
- Persistent test run storage in database — in-memory for now

## Decisions

### D1: Node outputs stored in run result (not database)

**Decision**: Keep test run results in-memory on the frontend. The backend returns all node outputs in the response.

**Rationale**: Test runs are ephemeral debugging artifacts. No need for database persistence.

### D2: Click node to see output panel

**Decision**: When a test run completes, clicking a node on the canvas opens a side panel showing that node's input, output, error, and duration.

**Rationale**: Non-intrusive UX. Users can inspect any node without cluttering the canvas.

### D3: Node status as badges (not canvas overlay)

**Decision**: Show node status as small colored badges on each node, not as a separate canvas overlay.

**Rationale**: Simpler implementation, works with existing React Flow node components.

## Risks / Trade-offs

- **[No real-time progress]** → Users see results only after full run completes. Mitigation: fast execution (mock mode).
- **[In-memory only]** → Run history lost on page refresh. Acceptable for debugging tool.
- **[Large output truncation]** → Node outputs could be large. Mitigation: truncate to 1000 chars with expand option.
