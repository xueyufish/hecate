## Context

Hecate's Model Hub phase 1 (Model Catalog 6.44, Lifecycle Manager 6.45, Intelligent Router 6.14) established the foundation: model discovery, staging channels, and semantic caching router. The existing `CostService` provides pricing CRUD and cost calculation from `TraceModel.usage`. `ModelRegistryModel.model_type` is a single string defaulting to `"chat"`. The frontend (`web/`) has a Next.js model management page with provider CRUD, model list, and a debug playground.

Industry research across 12+ platforms (vLLM, OpenAI, Bedrock, Vertex AI, watsonx, Salesforce Agentforce, Palantir AIP, Dify, LiteLLM, NullSpend, ai-finops-radar, OpenLLMetry) confirms clear patterns: agent platforms do NOT orchestrate inference servers (only cloud infra platforms do); fine-tuning is provider-API-based (not local training); LLM observability is converging on OpenTelemetry GenAI semantic conventions; model classification is moving from enums to structured metadata (modalities + capabilities).

## Goals / Non-Goals

**Goals:**
- Complete Model Hub with 5 remaining features (6.4, 6.11, 6.5, 6.6, O10+G4)
- Cost governance with configurable enforcement (alert or block)
- Multi-modal model classification that enables modality-aware routing
- External inference endpoint management (registration + health + metrics)
- Fine-tuning workflow via provider APIs with pluggable backend
- Monitoring console with trend visualization (full-stack: API + frontend)

**Non-Goals:**
- Inference server lifecycle orchestration (start/stop vLLM, GPU allocation, K8s scaling) — that is vLLM production-stack / AIBrix / KServe territory
- Local fine-tuning (GPU training) — we delegate to provider APIs (OpenAI, Bedrock)
- Quality regression detection — deferred to Sprint 7 (depends on Evaluation Suite)
- Multi-provider fine-tuning adapters (Bedrock, Vertex) — OpenAI is the reference; others added later via the same ABC

## Decisions

### D1: Model metadata schema — structured modalities JSON (Option B)

**Decision**: Replace `model_type: str` with `model_metadata: JSON` containing `{modalities: {input: [], output: []}, capabilities: {}, limits: {}}`.

**Rationale**: Structured modalities (input/output separation) naturally express multi-modal models (GPT-4o: input=[text,image,audio]) and generation models (DALL-E: output=[image]) without enumerating every combination. Aligns with basellm/llm-metadata and IETF ACPM draft. Dify's enum+features approach requires new enum values per new model category.

**Backward compatibility**: `model_type` column remains; `model_metadata` is additive. A computed accessor derives `model_type` from `model_metadata.modalities` for backward-compat reads. Migration populates `model_metadata` for existing rows based on current `model_type` value.

**Alternatives considered**: Dify-style enum (`model_type` + `features[]`) — simpler but extensible only by changing the enum; doesn't separate input/output modalities.

### D2: Cost budget enforcement — configurable policy via PreLLMHook

**Decision**: Budget enforcement uses `policy: "alert" | "block"` config. When `block`, a PreLLMHook checks budget before each LLM invocation and raises `BudgetExceededError` if exceeded.

**Rationale**: Pure alerting (post-hoc) risks runaway costs at 3 AM. Full blocking is too aggressive for development. Configurable policy lets each workspace choose. PreLLMHook is the existing extension point for pre-invocation interception. Pattern inspired by NullSpend's pre-request enforcement.

**Anomaly detection**: Z-score on rolling-window daily spend (30-day window, configurable). Same algorithm applied to performance metrics (latency, error rate) for drift detection in O10+G4.

**Alternatives considered**: Post-hoc alert only (risk of overrun); velocity circuit breaker (NullSpend pattern — more complex, deferred).

### D3: Inference endpoint management — registration + health, NOT orchestration

**Decision**: `InferenceBackendABC` with `health_check(endpoint)` and `invoke(endpoint, request)`. Hecate stores endpoint metadata (URL, model_id, backend_type) and polls `/health` periodically. Does NOT start/stop servers or manage GPUs.

**Rationale**: All surveyed agent platforms (Salesforce BYOLLM, Palantir AIP, Dify, Claude Code) connect to external endpoints without managing infrastructure. Only cloud platforms (Bedrock, Vertex AI, watsonx) manage inference — because infrastructure IS their product. vLLM production-stack handles orchestration; Hecate consumes its OpenAI-compatible API.

**Alternatives considered**: Full inference orchestration (vLLM lifecycle management) — would require K8s integration, GPU scheduling, and duplicate vLLM production-stack functionality.

### D4: Fine-tuning — provider API orchestration with ABC

**Decision**: `FineTuningBackendABC` with `submit_job`, `poll_status`, `cancel_job`, `get_result`. OpenAI adapter is the reference implementation. InMemory stub for testing. Other providers (Bedrock, Vertex) added later via same interface.

**Rationale**: Fine-tuning APIs differ radically across providers (OpenAI: JSONL upload; Bedrock: S3+IAM+VPC; Vertex: different SDK). No universal abstraction exists in the ecosystem (LiteLLM's fine-tuning module only covers OpenAI+Azure+Anthropic). ABC normalizes the workflow (upload → create → poll → deploy) while adapters handle provider specifics.

**Dataset storage**: `DatasetModel` stores metadata + file reference (MinIO URL or upload path). Actual file content stored in MinIO (already deployed), not in database.

**Alternatives considered**: Local training (needs GPU infrastructure); multi-provider from day one (Bedrock/Vertex complexity without immediate need).

### D5: Monitoring console — full-stack with Recharts

**Decision**: Backend aggregation APIs (`/api/monitoring/models/{id}/performance`, `/api/monitoring/cost/trends`) + frontend Dashboard pages using Recharts (bundled via shadcn/ui Chart components).

**Rationale**: shadcn/ui already includes Recharts-based chart components — zero additional dependency, style consistency. The needed charts (line trends, bar comparisons, donut breakdowns) are standard. ECharts' advanced features (heatmap, network graph) are unnecessary.

**Drift detection**: Z-score on performance time series (same algorithm as cost anomaly detection, different metric input).

**Quality regression**: Deferred to Sprint 7. Recorded as TBD in roadmap under Evaluation Suite.

**Alternatives considered**: ECharts (heavier, inconsistent with shadcn/ui); API-only delivery without frontend (repo is full-stack, not backend-only).

### D6: Module structure

New code lives in existing `src/hecate/model_hub/` alongside phase 1 modules:

```
model_hub/
├── catalog_service.py        (existing — 6.44)
├── lifecycle_service.py      (existing — 6.45)
├── intelligent_router.py     (existing — 6.14)
├── cache.py                  (existing)
├── cost_management.py        (NEW — 6.4: budgets, anomaly, forecasting)
├── inference_manager.py      (NEW — 6.5: endpoint registration, health)
├── fine_tuning.py            (NEW — 6.6: job orchestration, ABC)
└── monitoring.py             (NEW — O10+G4: aggregation queries)
```

New models in `src/hecate/models/`. New API routes in `src/hecate/api/management/`. Frontend pages under `web/src/app/(dashboard)/settings/models/`.

## Risks / Trade-offs

- **[model_metadata migration]** Existing rows have `model_type="chat"` only → Migration populates `model_metadata` with conservative defaults `{modalities: {input: ["text"], output: ["text"]}, capabilities: {}, limits: {}}`. Users can enrich metadata per model manually or via provider sync.

- **[Budget enforcement latency]** PreLLMHook budget check adds one DB query per LLM invocation → Query is indexed by (workspace_id, model_id, period). Sub-millisecond overhead. Acceptable.

- **[Inference health check false positives]** External endpoints may briefly become unreachable → Configurable retry (3 attempts, 5s interval) before marking unhealthy. Health status is informational, not blocking (requests route to healthy endpoints but don't fail if one is transiently down).

- **[Fine-tuning job long-running]** Provider fine-tuning jobs can take hours → Async polling with configurable interval (default 60s). Job state persisted in DB. Webhook notification when job completes (optional).

- **[Recharts bundle size]** ~100KB added to frontend → Lazy-loaded only on monitoring pages. Acceptable for admin console.
