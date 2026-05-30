## 1. Engine Layer — Agent Execution Foundation

- [x] 1.1 Add `agent_execute` abstract method to `EnginePort` in `src/hecate/engine/ports.py` with signature `async def agent_execute(self, agent_id: UUID, messages: list[dict], channel_snapshot: dict, context: dict | None = None) -> dict`
- [x] 1.2 Implement `AgentWorker` in `src/hecate/engine/workers/agent_worker.py` — a `Worker` subclass that handles AGENT-type nodes by calling `port.agent_execute()` with agent_id from node config
- [x] 1.3 Add `invocation_mode` field support to AGENT node config parsing in `graph_dsl.py` — accept `"tool"` or `"direct"` (default)
- [x] 1.4 Update `graph-dsl.schema.json` to add `invocation_mode`, `agent_id` fields to agent node config schema
- [x] 1.5 Add handoff edge trigger support — parse `type: "handoff"` field on edges as `trigger="handoff"` in `Edge` dataclass
- [x] 1.6 Add handoff cycle detection in `GraphCompiler._detect_unreachable()` — raise `GraphCompilationError` on circular handoff chains

## 2. Service Layer — Agent Execution & Handoff

- [x] 2.1 Implement `EnginePortAdapter.agent_execute()` in services layer — resolves `AgentModel` by ID, builds isolated context (persona + tools + knowledge bases), invokes LLM via `ConversationService`, returns response dict
- [x] 2.2 Create `src/hecate/services/orchestration/handoff.py` — `HandoffToolProvider` that generates `handoff_to_agent` tool schema and injects it into agent tool lists based on graph handoff edges
- [x] 2.3 Implement handoff tool execution — when LLM calls `handoff_to_agent(target=X)`, return `Command(goto=X)` via `WorkerResult`
- [x] 2.4 Create `src/hecate/services/orchestration/agent_tool.py` — `AgentToolProvider` that exposes target agents as callable tools when `invocation_mode: "tool"`, generates tool schema from agent persona
- [x] 2.5 Implement agent-as-tool execution — when parent LLM calls `agent_{name}` tool, execute target agent via `agent_execute()` and return result as tool response
- [x] 2.6 Wire `AgentWorker` into `PregelRuntime` — update `WorkflowTestRunner._TestWorker` to use real `AgentWorker` when not in mock mode

## 3. API Layer — Orchestration Templates

- [x] 3.1 Create `src/hecate/api/management/orchestration_templates.py` — `GET /api/orchestration-templates` listing endpoint returning template metadata (id, name, description, category, preview)
- [x] 3.2 Add `GET /api/orchestration-templates/{template_id}` detail endpoint returning full Graph DSL JSON
- [x] 3.3 Register orchestration template router in `src/hecate/main.py`
- [x] 3.4 Create template data files in `src/hecate/data/orchestration_templates/` — `customer-service-triage.json`, `content-pipeline.json`, `hierarchical-supervisor.json`

## 4. Tests — Backend

- [x] 4.1 Create `tests/test_engine/test_agent_worker.py` — test AgentWorker with mock port (valid agent_id, missing agent_id, context isolation)
- [x] 4.2 Create `tests/test_engine/test_handoff.py` — test handoff tool generation, Command(goto) result, cycle detection
- [x] 4.3 Add handoff edge parsing tests to `tests/test_engine/test_graph_dsl.py` — parse handoff trigger, validate source/target are agent nodes
- [x] 4.4 Create `tests/test_services/test_orchestration/test_agent_tool.py` — test AgentToolProvider schema generation and tool execution
- [x] 4.5 Create `tests/test_api/test_orchestration_templates.py` — test list templates, get template detail, 404 for missing template
- [x] 4.6 Create `tests/test_api/test_e2e_multi_agent.py` — E2E test: create agents → build multi-agent graph → test-run with mock → verify agent node execution order

## 5. Frontend — Canvas Multi-Agent Support

- [x] 5.1 Update `web/src/lib/dsl-bridge.ts` — support handoff edge trigger in `dslToReactFlow()` and `reactFlowToDsl()` bidirectional conversion
- [x] 5.2 Update `web/src/components/workflow/node-types.tsx` — enhance AgentNode component to show agent name, model, and invocation mode badge
- [x] 5.3 Create `web/src/components/workflow/agent-palette.tsx` — sidebar panel listing available agents from `GET /api/agents`, draggable onto canvas
- [x] 5.4 Update `web/src/components/workflow/canvas-area.tsx` — add agent drop handler, edge type selection dialog (handoff vs invoke-as-tool), dashed edge rendering for handoff
- [x] 5.5 Create `web/src/components/workflow/template-picker.tsx` — modal dialog loading templates from `GET /api/orchestration-templates`, applying selected template to canvas
- [x] 5.6 Update `web/src/app/(dashboard)/workflows/[id]/page.tsx` — add template picker button to toolbar, integrate agent palette into sidebar, add execution state highlighting during test runs

## 6. Documentation & Integration

- [x] 6.1 Update `schemas/graph-dsl.schema.json` with handoff edge type and agent node config fields
- [x] 6.2 Update `docs/features/feature-catalog.md` — mark 2.1, 2.2, 2.7 as implemented
- [x] 6.3 Run full test suite, ruff, mypy — ensure all green
