## 1. Model Metadata Schema (6.11)

- [x] 1.1 Add `model_metadata: Mapped[dict]` JSON column to `ModelRegistryModel` in `src/hecate/models/model_provider.py`
- [x] 1.2 Create Alembic migration to add `model_metadata` column with backfill for existing rows (chat → `{modalities: {input: ["text"], output: ["text"]}, capabilities: {}, limits: {}}`)
- [x] 1.3 Add `ModelMetadataSchema` Pydantic model with `modalities`, `capabilities`, `limits` fields and validation
- [x] 1.4 Add backward-compatible `model_type` computed property that derives type from `model_metadata`
- [x] 1.5 Update `CatalogService` to filter and display models by capability badges (vision, tool_call, reasoning, context size)
- [x] 1.6 Update frontend model list component (`web/src/app/(dashboard)/settings/models/page.tsx`) to render capability badges from `model_metadata`
- [x] 1.7 Write tests: metadata schema validation, backward compat accessor, migration backfill, catalog badge display

## 2. Modality-Aware Routing (llm-routing modification)

- [x] 2.1 Add modality filtering to `ModelRouter` — filter candidates by required input modalities before applying cost/latency strategy
- [x] 2.2 Add capability filtering — prefer `tool_call: true` models when request includes tool definitions
- [x] 2.3 Add `NoCapableModelError` exception for when no candidate supports required modalities
- [x] 2.4 Write tests: image input routes to vision model, tool request filters non-tool models, no capable model raises error

## 3. Cost Budget Management (6.4)

- [x] 3.1 Create `ModelCostBudgetModel` in `src/hecate/models/` with fields: scope (workspace/agent/user), target_id, limit_amount, period, currency, policy (alert/block), workspace_id
- [x] 3.2 Create Alembic migration for `model_cost_budgets` table
- [x] 3.3 Create `CostBudgetService` in `src/hecate/model_hub/cost_management.py` — budget CRUD, current spend calculation, enforcement check
- [x] 3.4 Implement hierarchical budget resolution — agent budget overrides workspace budget; user budget overrides agent budget
- [x] 3.5 Implement `BudgetEnforcementHook` as `PreLLMHook` subclass — checks budget before LLM invocation, raises `BudgetExceededError` when policy is "block" and budget exceeded
- [x] 3.6 Write tests: budget CRUD, hierarchical resolution, enforcement hook (block vs alert), period reset

## 4. Cost Anomaly Detection & Forecasting (6.4)

- [x] 4.1 Implement z-score anomaly detection on daily spend per model (30-day rolling window, configurable threshold default 2.5)
- [x] 4.2 Implement spend forecasting using linear regression on daily spend — returns projected amount, confidence interval, projected overrun
- [x] 4.3 Implement chargeback report generation — aggregate by agent/project dimension with per-model breakdown and period-over-period comparison
- [x] 4.4 Add anomaly severity classification (`info` / `warn` / `critical`) based on z-score magnitude
- [x] 4.5 Add cold-start guard — skip anomaly detection when fewer than 7 days of historical data
- [x] 4.6 Write tests: normal spend not flagged, spike detected, cold start skipped, forecast under/over budget, chargeback aggregation

## 5. Cost Management API (6.4)

- [x] 5.1 Create API routes in `src/hecate/api/management/cost_management.py`: `POST/GET/PUT/DELETE /api/models/cost/budgets`, `GET /api/models/cost/anomalies`, `GET /api/models/cost/forecast`, `GET /api/models/cost/chargeback`
- [x] 5.2 Register cost management router in `src/hecate/main.py`
- [x] 5.3 Add `CostManagementSettings` to `src/hecate/core/config.py` (anomaly threshold, rolling window days, default policy)
- [x] 5.4 Write API integration tests: budget lifecycle, anomaly listing, forecast retrieval, chargeback report

## 6. Inference Endpoint Management (6.5)

- [x] 6.1 Define `InferenceBackendABC` in `src/hecate/model_hub/inference_manager.py` with abstract methods `health_check(endpoint)` and `invoke(endpoint, request)`
- [x] 6.2 Implement `OpenAICompatibleBackend` — handles health via `/health`, models via `/v1/models`, invocation via `/v1/chat/completions`
- [x] 6.3 Create `InferenceEndpointModel` in `src/hecate/models/` with fields: url, model_id, backend_type, auth_config, health_status, last_health_at, workspace_id
- [x] 6.4 Create Alembic migration for `inference_endpoints` table
- [x] 6.5 Implement `InferenceManager` service — endpoint CRUD, periodic health polling (asyncio task, configurable interval), retry logic (3 attempts before marking unreachable)
- [x] 6.6 Implement health-based routing — route invocations to healthy endpoints only, fallback to alternative providers when all endpoints unreachable
- [x] 6.7 Implement Prometheus metrics scraping from endpoints that expose `/metrics` (TTFT, throughput, error rate)
- [x] 6.8 Create API routes in `src/hecate/api/management/inference.py`: `POST/GET/PUT/DELETE /api/inference/endpoints`, `GET /api/inference/endpoints/{id}/health`
- [x] 6.9 Register inference router in `src/hecate/main.py`
- [x] 6.10 Write tests: ABC not instantiable, OpenAICompatibleBackend health check, endpoint CRUD, health polling, unreachable marking, routing to healthy only

## 7. Fine-Tuning Pipeline (6.6)

- [x] 7.1 Define `FineTuningBackendABC` in `src/hecate/model_hub/fine_tuning.py` with abstract methods: `submit_job`, `poll_status`, `cancel_job`, `get_result`
- [x] 7.2 Create `DatasetModel` in `src/hecate/models/` with fields: name, description, format, version, row_count, schema_preview, file_storage_url, workspace_id
- [x] 7.3 Create `FineTuningJobModel` in `src/hecate/models/` with fields: dataset_id, base_model, provider, provider_job_id, status, config, result_model_id, metrics, error_message, workspace_id
- [x] 7.4 Create Alembic migration for `datasets` and `fine_tuning_jobs` tables
- [x] 7.5 Implement `DatasetService` — CRUD, file upload to MinIO, versioning, preview (first 10 rows)
- [x] 7.6 Implement `OpenAIFineTuningBackend` — upload via `/v1/files`, create job via `/v1/fine_tuning/jobs`, poll status, retrieve result
- [x] 7.7 Implement `InMemoryFineTuningBackend` — test stub that simulates job lifecycle (queued → running → succeeded)
- [x] 7.8 Implement `FineTuningService` — orchestrates backend calls, persists job state, async polling loop (configurable interval)
- [x] 7.9 Implement one-click deploy — on job success, register fine-tuned model in `ModelRegistryModel` with metadata linking to base model
- [x] 7.10 Create API routes in `src/hecate/api/management/fine_tuning.py`: dataset CRUD + upload, job CRUD + submit/cancel, `POST /api/fine-tuning/jobs/{id}/deploy`
- [x] 7.11 Register fine-tuning router in `src/hecate/main.py`
- [x] 7.12 Write tests: ABC not instantiable, dataset CRUD + upload, InMemory backend job lifecycle, OpenAI backend API calls (mocked), deploy registration

## 8. Monitoring Aggregation Backend (O10+G4)

- [x] 8.1 Implement `MonitoringService` in `src/hecate/model_hub/monitoring.py` — aggregate TraceModel into per-model performance metrics (avg latency, TTFT, error rate, request count, cost)
- [x] 8.2 Implement time-series aggregation with configurable granularity (hourly/daily/weekly) and date range filtering
- [x] 8.3 Implement model comparison aggregation — side-by-side metrics for multiple models
- [x] 8.4 Implement performance drift detection — z-score on daily latency and error rate (same algorithm as cost anomaly detection, different metric input)
- [x] 8.5 Extend cost-dashboard API with per-model trend time-series endpoint (`GET /api/cost-dashboard/trends?group_by=model`)
- [x] 8.6 Create API routes in `src/hecate/api/management/monitoring.py`: `GET /api/monitoring/models/{id}/performance`, `GET /api/monitoring/models/compare`, `GET /api/monitoring/models/{id}/drift`
- [x] 8.7 Register monitoring router in `src/hecate/main.py`
- [x] 8.8 Write tests: performance aggregation, time-series granularity, comparison matrix, drift detection on latency spike

## 9. Monitoring Console Frontend (O10+G4)

- [x] 9.1 Add Recharts Chart components from shadcn/ui to `web/` (LineChart, BarChart, DonutChart wrappers)
- [x] 9.2 Create monitoring dashboard page at `web/src/app/(dashboard)/settings/models/monitoring/page.tsx` — model selector, time range picker, metric toggle, trend line charts (latency, cost, error rate)
- [x] 9.3 Create model comparison view component — table with per-model rows, metric columns, capability badges
- [x] 9.4 Create cost analysis page at `web/src/app/(dashboard)/settings/models/cost-analysis/page.tsx` — per-model cost bar chart, budget utilization gauge, anomaly timeline, forecast projection
- [x] 9.5 Add drift alert feed component — list of recent drift events with severity indicators
- [x] 9.6 Add budget exceeded warning banner to dashboard layout when workspace spend exceeds 80% of monthly budget
- [x] 9.7 Update sidebar navigation to include links to monitoring dashboard and cost analysis pages
- [x] 9.8 Write frontend component tests: chart rendering with mock data, model selector interaction, comparison table population

## 10. Integration & Verification

- [x] 10.1 Verify all new modules pass `ruff check src/hecate/ tests/`
- [x] 10.2 Verify all new modules pass `ruff format --check src/ tests/`
- [x] 10.3 Verify all new modules pass `mypy src/`
- [x] 10.4 Run targeted test suite for all new test files
- [x] 10.5 Run full test suite (`python -m pytest tests/ -q`) — verify zero regressions
- [x] 10.6 Update `docs/features/feature-catalog.md` — mark 6.4, 6.11, 6.5, 6.6, O10+G4 as ✅, update P3 statistics
- [x] 10.7 Update `docs/features/roadmap.md` — mark Sprint 5 Model Hub features as ✅ Done, update milestone M5 checkboxes
