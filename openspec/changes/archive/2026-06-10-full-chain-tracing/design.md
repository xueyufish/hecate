## Context

Hecate currently has four disconnected observability subsystems:

1. **EventStore** (`engine/eventstore.py`) — Append-only engine event log with 12 event types, wired into PregelRuntime (8 emit calls) and Workers (4 emit calls). In-memory implementation only.
2. **TracingService** (`services/observability/tracing.py`) — Complete TraceContext/SpanData API but pure in-memory stub, never called from production code.
3. **MetricsCollector** (`services/observability/metrics.py`) — Prometheus format metrics, instantiated fresh each request (stateless), never accumulates.
4. **StructuredLogger** (`services/observability/structured_logger.py`) — JSON logging with context enrichment, functional but never wired.

There is no `trace_id` that connects these systems. A request enters FastAPI, flows through Service → Engine → Worker, and exits — with zero correlation across layers. The engine layer has a strict zero-external-dependency policy (only `jsonschema` as exception), enforced by `EnginePort` as the boundary interface.

Industry research (Google Cloud, AWS, Microsoft Azure, Salesforce, IBM, Alibaba Cloud) confirms OpenTelemetry is the de facto standard for agent platform tracing. 6 of 8 major platforms use OTel as the underlying technology.

## Goals / Non-Goals

**Goals:**
- Establish a single `trace_id` that propagates from FastAPI middleware through Service → Engine → Worker layers
- Persist trace/span data to a PostgreSQL `traces` table with observation-centric design
- Enable PregelRuntime and Workers to create spans without importing OTel directly (via EnginePort abstraction)
- Correlate existing EventStore events with traces via `trace_id` field
- Provide REST API for trace list and detail queries
- Support optional export to external providers (LangFuse, OTel Collector) via async plugin system
- Maintain engine layer zero-external-dependency invariant

**Non-Goals:**
- Real-time trace streaming / WebSocket push (P3 — 8.2 Real-Time Monitoring)
- Trace UI / visualization dashboard (P3)
- Metrics integration (Prometheus counters/gauges from trace data) — leave MetricsCollector as-is
- StructuredLogger integration — leave as-is for now
- Distributed tracing across multiple Hecate instances (W3C Trace Context propagation) — future scope
- Trace-based auto-evaluation or quality scoring
- Data retention / cleanup policies for trace data

## Decisions

### D1: EventStore trace_id — Explicit parameter (not weak convention)

**Decision**: Add `trace_id: str | None = None` as an explicit parameter to `Event` dataclass and `EventStore.append()`.

**Rationale**: `trace_id` is the core correlation field for the tracing system. A weak convention (passing it in `payload` dict) allows callers to forget it silently, breaking trace integrity. The explicit parameter ensures compile-time visibility and test enforcement. Impact is manageable: `Event` dataclass gains one field with a default, `InMemoryEventStore.append()` passes it through, and all ~12 emit call sites in PregelRuntime/Workers pass `trace_id` from their execution context.

**Alternatives considered**:
- Weak convention (payload dict key) — rejected: no compile-time safety, easy to forget
- Separate `TracedEvent` subclass — rejected: unnecessary type proliferation

### D2: Engine-layer span creation — via EnginePort abstract method

**Decision**: Add `create_span(name, parent_id=None, attributes=None)` and `end_span(span_id, output=None, usage=None)` as abstract methods on `EnginePort`. The service-layer adapter provides the OTel implementation.

**Rationale**: EnginePort exists specifically to isolate the engine from external dependencies. Adding `opentelemetry-api` to the engine layer would violate the zero-external-dep principle and set a precedent for further dependency creep. The two existing implementations (`_ProductionEnginePort` in `engine_port_adapter.py`, `AgentExecutionPort` in `agent_execution_port.py`) must be updated, but this is the same pattern used for `llm_invoke`, `tool_execute`, etc.

**Alternatives considered**:
- Import `opentelemetry-api` directly in engine — rejected: violates architecture invariant
- Pass tracer object via constructor injection — rejected: leaks OTel types into engine signatures
- Callback function pattern — rejected: less discoverable than named abstract methods

### D3: Trace data model — Observation-centric single table

**Decision**: Single `traces` table with `parent_id` self-referencing foreign key, following the LangFuse v4 observation-centric model. Each row is a trace root or a child span.

**Table schema**:
```
traces (
  id              UUID PK DEFAULT gen_random_uuid()
  trace_id        UUID NOT NULL           -- shared by all records in one trace
  parent_id       UUID FK → traces.id     -- NULL for root spans
  type            VARCHAR(32) NOT NULL    -- 'trace', 'span', 'generation', 'tool', 'retrieval'
  name            VARCHAR(255) NOT NULL
  session_id      UUID                    -- FK to sessions (nullable for non-session traces)
  agent_id        UUID                    -- FK to agents
  user_id         UUID                    -- from auth context
  input_data      JSONB                   -- request/input content
  output_data     JSONB                   -- response/output content
  metadata        JSONB DEFAULT '{}'      -- model, latency, etc.
  usage           JSONB                   -- {input_tokens, output_tokens, cost_usd}
  level           VARCHAR(16) DEFAULT 'DEFAULT'  -- DEBUG, DEFAULT, WARNING, ERROR
  status          VARCHAR(16) DEFAULT 'started'   -- started, completed, error
  start_time      TIMESTAMPTZ NOT NULL
  end_time        TIMESTAMPTZ
  created_at      TIMESTAMPTZ DEFAULT now()
)
```

**Indexes**: `ix_traces_trace_id` on `trace_id`, `ix_traces_session_id` on `session_id`, `ix_traces_agent_id` on `agent_id`, `ix_traces_parent_id` on `parent_id`.

**Rationale**: Single table with self-referencing is simpler than separate trace/span tables. Matches LangFuse v4's proven model. PostgreSQL handles the hierarchical queries well with recursive CTEs for span tree reconstruction.

**Alternatives considered**:
- Separate `traces` + `spans` tables — rejected: requires JOINs, more complex
- No local persistence, export-only — rejected: requires external provider for any visibility
- Append-only event log (like EventStore) — rejected: mutable status/usage fields needed

### D4: Context propagation — OTel contextvars

**Decision**: Use OpenTelemetry's built-in `contextvars` propagation. `FastAPIInstrumentor` creates the root span per HTTP request. `trace_id` is extracted from the active OTel span context and passed explicitly to EnginePort methods and EventStore calls.

**Rationale**: This is the industry standard (confirmed by Google, AWS, Microsoft, Salesforce, IBM, Alibaba). OTel contextvars automatically propagate through async code in Python 3.12+. No manual context passing between async layers is needed — but for the engine layer, we extract `trace_id` from contextvars at the service boundary and pass it explicitly via method parameters, maintaining engine's independence from OTel.

### D5: OpsTraceManager — Async queue with provider plugins

**Decision**: Implement `OpsTraceManager` as a singleton service with an async queue. Trace/span writes go to both the local `traces` table (synchronous, immediate) and an optional async dispatch to configured providers.

**Provider interface**:
```python
class TraceProvider(ABC):
    async def on_trace_start(self, trace: TraceRecord) -> None: ...
    async def on_span_start(self, span: SpanRecord) -> None: ...
    async def on_span_end(self, span: SpanRecord) -> None: ...
    async def flush(self) -> None: ...
```

**Built-in providers**: `LangFuseProvider`, `OTelProvider` (sends OTLP). Additional providers can be added via plugin registration.

**Rationale**: Following the Dify `OpsTraceManager` pattern. Async dispatch ensures tracing never blocks the request path. Local persistence guarantees visibility even without an external provider.

### D6: FastAPI integration — Minimal OTel instrumentation

**Decision**: Use `opentelemetry-instrumentation-fastapi` for automatic HTTP request tracing. Configure in `main.py` lifespan. Add custom attributes (`agent_id`, `session_id`, `user_id`) from request state.

**Rationale**: One-line setup, zero manual span creation at the API layer. Standard OTel attributes for HTTP spans. Custom business attributes added via middleware or dependency injection.

## Risks / Trade-offs

- **[Risk] PostgreSQL trace volume at high QPS** → Mitigation: Add configurable sampling rate (e.g., 10% of traces). Add TTL cleanup job in future. For P2, expect <100 RPM which PostgreSQL handles easily.
- **[Risk] New OTel dependency footprint** → Mitigation: `opentelemetry-api` and `opentelemetry-sdk` are lightweight pure-Python packages. Isolate in `[observability]` optional dependency group so core install stays lean.
- **[Risk] EnginePort ABC expansion** → Mitigation: Only 2 new methods, following established pattern of optional methods with defaults. Both implementations already exist and will be updated.
- **[Risk] Trace data contains PII (user prompts, model responses)** → Mitigation: `input_data`/`output_data` fields are optional and can be redacted via configuration. Leverage existing security-layer `StreamSanitizer` for sensitive data removal before trace storage.
- **[Trade-off] Single traces table vs separate trace/span tables** → Accepted: simpler schema, but span tree queries require recursive CTEs. Performance acceptable at P2 scale.
- **[Trade-off] Local persistence + async export vs export-only** → Accepted: more storage overhead, but ensures zero-dependency observability out of the box.

## Open Questions

- Should trace data be scoped to workspaces? Current design uses `session_id`/`agent_id` for filtering, but multi-tenant workspace isolation may require a `workspace_id` column.
- Sampling strategy: fixed percentage vs adaptive (based on error rate or latency)? Propose: start with 100% (record everything), add configurable sampling in P3.
- Trace retention policy: how long to keep trace data? Propose: 30 days default with configurable TTL, implemented as a future cleanup cron.
