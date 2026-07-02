## Why

The platform currently has 9 evaluators (4 RAG + 5 Agent) and basic dataset/run/score infrastructure. To deliver enterprise-grade evaluation capabilities, we need to expand to 40+ built-in evaluators covering result quality, process correctness, interaction coherence, and safety — and add regression test infrastructure with dataset versioning, assertion-based pass/fail, run comparison, and CI/CD integration. This completes features 7.2a and 7.6, enabling teams to detect quality regressions before deployment and continuously measure Agent performance across releases.

## What Changes

- Expand evaluator library from 9 to 41 evaluators across four categories: Result Layer (output quality), Process Layer (tool/reasoning), Interaction Layer (multi-turn), and Generic/Programmatic (deterministic, LLM-judge, code execution, safety)
- Refactor evaluator registry from inline dict to a structured package with auto-registration decorator and category-based file organization
- Standardize LLM-as-Judge prompt templates with a `JudgePromptTemplate` schema (scoring_scale, system_prompt, user_prompt_template, output_format)
- Extend `EvalInput` with optional fields: `conversation_history`, `system_prompt`, `agent_id`, `session_id` for multi-turn and process evaluators
- Add dataset versioning: `version` tag, `baseline_run_id` for regression comparison, `is_locked` to freeze golden datasets
- Add per-item assertion model: `assertions` JSON array on `EvaluationItemModel` (type + threshold), with Dataset-level default threshold fallback
- Add run comparison API: POST `/api/evaluation/runs/compare` returning per-metric delta and regression flags (score drop > configurable threshold)
- Add regression trigger API: POST `/api/evaluation/regression/run` for CI/CD integration (returns pass/fail + report)
- Add evaluator listing API: GET `/api/evaluation/evaluators` returning all registered evaluators with category, description, and required input fields
- Integrate evaluation regression with 8.6 Alerting: `evaluation_regression` alert type triggers when run scores drop below baseline
- Evaluation LLM calls use a separate cost tracking marker (`purpose=evaluation`) to avoid consuming user quota

## Capabilities

### New Capabilities
- `builtin-evaluators`: 32 new built-in evaluators across four layers (Result, Process, Interaction, Generic/Programmatic) with standardized LLM-as-Judge prompt templates and auto-registration registry
- `regression-testing`: Dataset versioning, per-item assertion model with thresholds, run comparison with regression detection, and CI/CD integration API for automated quality gating

### Modified Capabilities
- `evaluation-framework`: EvalInput expansion (conversation_history, system_prompt, agent_id, session_id), registry refactor to category-based package with decorator registration, deterministic evaluator parallel execution optimization
- `evaluation-dataset`: Dataset versioning fields (version, baseline_run_id, is_locked), item assertion JSON field, item tags for grouping
- `evaluation-api`: New endpoints for evaluator listing, run comparison, regression trigger, and dataset versioning
- `alerting`: New `evaluation_regression` alert type for score regression detection

## Impact

- **New files**: `services/evaluation/evaluators/` package (format.py, content.py, citation.py, tool.py, multi_turn.py, judge.py, safety.py, programmatic.py), `services/regression_service.py`, `services/evaluation/prompt_templates.py`
- **Modified models**: `models/evaluation.py` (version, baseline_run_id, is_locked on DatasetModel; assertions, tags on ItemModel), `models/alert.py` (new AlertType)
- **Modified services**: `services/evaluation/evaluator.py` (registry refactor), `services/evaluation/types.py` (EvalInput expansion), `services/evaluation/engine.py` (parallel deterministic execution)
- **Modified API**: `api/evaluation.py` (new endpoints), `api/management/alerts.py` (new signal provider)
- **Migration**: New Alembic migration for dataset versioning + item assertion columns
- **Dependencies**: No new external packages — all evaluators use existing LLMService or deterministic logic
- **Tests**: Per-evaluator unit tests + regression comparison tests + CI/CD API tests
