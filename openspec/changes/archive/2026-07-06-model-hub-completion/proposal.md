## Why

Hecate's Model Hub phase 1 delivered Model Catalog (6.44), Lifecycle Manager (6.45), and Intelligent Router (6.14) — covering model discovery, staging, and routing. However, five critical capabilities remain incomplete: cost governance lacks anomaly detection and budget enforcement; model classification is a single `model_type` string insufficient for multi-modal routing; self-hosted inference endpoints have no registration or health monitoring; fine-tuning workflow is entirely absent; and the Model Management Console has no monitoring dashboard. These gaps prevent Hecate from serving as a complete model operations platform.

## What Changes

- **Model Cost Management (6.4 G8)**: Add per-model and per-workspace cost budgets with z-score anomaly detection, configurable enforcement policy (`alert` or `block` via PreLLMHook), spend forecasting, and chargeback reports. Integrates with existing BudgetModel (10.7).
- **Multi-Modal Model Classification (6.11 G6)**: Replace `model_type: str` with structured `model_metadata` JSON containing `modalities` (input/output arrays), `capabilities` (boolean flags: reasoning, tool_call, vision), and `limits` (context, output). Router and Catalog become modality-aware.
- **Managed Model Deployment (6.5 G5)**: Register external inference endpoints (vLLM/Ollama/OpenAI-compatible), periodic `/health` polling, Prometheus metrics collection (TTFT, throughput, error rate), and model-to-endpoint routing. Hecate does NOT orchestrate inference server lifecycle — only manages endpoint metadata and health.
- **Fine-Tuning Pipeline (6.6 G7)**: Full workflow via provider APIs — dataset management (upload, version, preview), FineTuningBackendABC abstraction, OpenAI reference adapter, async job state tracking (queued → running → succeeded/failed), post-tune model registration, one-click deploy.
- **Model Management Console + Monitoring (O10+G4)**: Full-stack delivery — backend aggregation APIs (per-model performance, cost trends, drift detection) + frontend Console/Dashboard pages (performance comparison view, cost analysis charts, trend visualizations using Recharts). Performance drift detection reuses z-score from 6.4. Quality regression detection deferred to Sprint 7 (TBD, recorded in roadmap).

## Capabilities

### New Capabilities
- `model-cost-management`: Per-model/workspace cost budgets, z-score anomaly detection, configurable enforcement (alert/block), spend forecasting, chargeback reports
- `model-metadata-schema`: Structured model classification replacing single model_type string — modalities (input/output), capabilities (reasoning/tool_call/vision), limits (context/output)
- `inference-endpoint-management`: External inference endpoint registration, health check polling, Prometheus metrics collection, model-to-endpoint routing, InferenceBackendABC
- `fine-tuning-pipeline`: DatasetModel management, FineTuningBackendABC, OpenAI adapter, async job orchestration, post-tune model registration and deployment
- `model-monitoring-console`: Backend performance/cost/error-rate aggregation APIs + frontend Console/Dashboard pages with trend charts, performance comparison, drift detection

### Modified Capabilities
- `cost-dashboard`: Extended with per-model cost breakdown and time-series trend endpoints consumed by the monitoring console
- `llm-routing`: Extended with modality-aware model selection — router filters candidates by required input/output modalities and capability flags before applying cost/latency strategy

## Impact

- **New backend modules**: `src/hecate/model_hub/cost_management.py`, `model_hub/inference_manager.py`, `model_hub/fine_tuning.py`, `model_hub/monitoring.py`
- **New models**: `InferenceEndpointModel`, `DatasetModel`, `FineTuningJobModel`, `ModelCostBudgetModel`
- **Modified models**: `ModelRegistryModel` (add `model_metadata` JSON field), `ModelPricingModel` (no schema change, new queries)
- **New ABCs**: `InferenceBackendABC` (health_check, invoke), `FineTuningBackendABC` (submit_job, poll_status, cancel_job, get_result)
- **New API endpoints**: `/api/models/cost/budgets`, `/api/models/cost/anomalies`, `/api/models/cost/forecast`, `/api/inference/endpoints`, `/api/fine-tuning/datasets`, `/api/fine-tuning/jobs`, `/api/monitoring/models/{id}/performance`
- **Frontend**: New pages under `web/src/app/(dashboard)/settings/models/` — console dashboard, cost analysis, monitoring trends; Recharts integration
- **Database migrations**: 4 new tables + 1 column addition (model_metadata)
- **New dependencies**: `recharts` (frontend, already bundled via shadcn/ui), no new Python packages (OpenAI fine-tuning via existing `openai` or `litellm`)
