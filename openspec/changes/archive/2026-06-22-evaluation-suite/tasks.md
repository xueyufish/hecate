## 1. Registry Refactor & Infrastructure

- [x] 1.1 Create `services/evaluation/registry.py` with `EvaluatorRegistry` class, `@register_evaluator(name)` decorator, `get_evaluator(name)`, `list_evaluators(category=None)`, and `list_evaluator_names()` functions
- [x] 1.2 Create `services/evaluation/prompt_templates.py` with `JudgePromptTemplate` dataclass (scoring_scale, system_prompt, user_prompt_template, output_format, scoring_rubric) and a library of pre-built templates
- [x] 1.3 Create `services/evaluation/evaluators/` package directory with `__init__.py` that imports all evaluator modules to trigger auto-registration
- [x] 1.4 Move existing 4 RAG evaluators from `rag_evaluators.py` to `evaluators/rag.py` with `@register_evaluator` decorator
- [x] 1.5 Move existing 5 Agent evaluators from `agent_evaluators.py` to `evaluators/agent.py` with `@register_evaluator` decorator
- [x] 1.6 Add re-export shims in original `rag_evaluators.py` and `agent_evaluators.py` for backward compatibility
- [x] 1.7 Update `api/evaluation.py` to use new registry functions instead of inline `_EVALUATOR_REGISTRY` dict
- [x] 1.8 Add `category` class attribute to `Evaluator` ABC with default value `"generic"`

## 2. EvalInput Expansion

- [x] 2.1 Add optional fields to `EvalInput` dataclass: `conversation_history: list[dict]`, `system_prompt: str | None`, `agent_id: uuid.UUID | None`, `session_id: uuid.UUID | None` (all defaulting to None/empty)
- [x] 2.2 Update `EvaluationEngine.run()` to populate new EvalInput fields from EvaluationItemModel metadata when available

## 3. Deterministic Format Evaluators (Result Layer)

- [x] 3.1 Create `evaluators/format.py` with `ExactMatchEvaluator` — compares generated_answer against expected_answer for exact match
- [x] 3.2 Implement `ContainsEvaluator` — checks if generated_answer contains a specified substring
- [x] 3.3 Implement `ContainsAnyEvaluator` — checks if generated_answer contains any of specified substrings
- [x] 3.4 Implement `RegexMatchEvaluator` — checks if generated_answer matches a regex pattern
- [x] 3.5 Implement `IsJSONEvaluator` — validates that generated_answer is valid JSON
- [x] 3.6 Implement `FormatCheckEvaluator` — validates output format against a schema (key presence, type checks)
- [x] 3.7 Implement `BLEUScoreEvaluator` — standard BLEU score computation (deterministic, no LLM)
- [x] 3.8 Implement `ROUGEScoreEvaluator` — standard ROUGE-L score computation
- [x] 3.9 Implement `F1ScoreEvaluator` — token-level F1 score between generated and expected answers

## 4. Content Quality Evaluators (Result Layer, LLM-Judge)

- [x] 4.1 Create `evaluators/content.py` with `ToxicityDetectionEvaluator` using 5-point scoring rubric JudgePromptTemplate
- [x] 4.2 Implement `SafetyHarmlessnessEvaluator` — assesses if output is safe and harmless
- [x] 4.3 Implement `InstructionFollowingEvaluator` — checks if system_prompt instructions were followed
- [x] 4.4 Implement `CoherenceEvaluator` — assesses internal logical coherence of the response
- [x] 4.5 Implement `FluencyEvaluator` — assesses language fluency and readability

## 5. Citation & Grounding Evaluators (Result Layer, LLM-Judge)

- [x] 5.1 Create `evaluators/citation.py` with `CitationRelevanceEvaluator` — checks if citations in the answer are relevant to the query
- [x] 5.2 Implement `SourceAttributionEvaluator` — verifies proper source attribution in generated answers
- [x] 5.3 Implement `GroundednessCheckEvaluator` — checks if all claims are grounded in retrieved contexts
- [x] 5.4 Implement `HallucinationDetectionEvaluator` — detects fabricated claims not supported by context

## 6. Tool & Process Evaluators (Process Layer)

- [x] 6.1 Create `evaluators/tool.py` with `ToolSelectionAccuracyEvaluator` — assesses tool selection correctness (LLM-judge)
- [x] 6.2 Implement `ToolTrajectoryScoringEvaluator` — scores the sequence of tool calls (LLM-judge)
- [x] 6.3 Implement `ToolParameterAccuracyEvaluator` — evaluates correctness of tool call parameters
- [x] 6.4 Implement `ToolOrderCorrectnessEvaluator` — checks if tool call order is logical
- [x] 6.5 Implement `ReasoningQualityEvaluator` — assesses overall reasoning quality
- [x] 6.6 Implement `StepValidityEvaluator` — validates individual reasoning steps

## 7. Multi-turn Interaction Evaluators (Interaction Layer)

- [x] 7.1 Create `evaluators/multi_turn.py` with `MultiTurnSuccessEvaluator` — evaluates task completion across turns
- [x] 7.2 Implement `MultiTurnCoherenceEvaluator` — checks consistency across conversation turns
- [x] 7.3 Implement `ConversationQualityEvaluator` — overall conversation quality assessment
- [x] 7.4 Implement `ContextRetentionEvaluator` — evaluates if earlier context is retained in later turns

## 8. Generic LLM-Judge Evaluators (Generic Layer)

- [x] 8.1 Create `evaluators/judge.py` with `SemanticSimilarityEvaluator` — measures semantic equivalence between answer and expected
- [x] 8.2 Implement `RubricScoringEvaluator` — generic rubric-based scoring with configurable rubric
- [x] 8.3 Implement `FactualityCheckEvaluator` — checks factual accuracy of claims
- [x] 8.4 Implement `LLMRubricEvaluator` — accepts custom rubric string for domain-specific evaluation

## 9. Safety & Security Evaluators (Generic Layer)

- [x] 9.1 Create `evaluators/safety.py` with `PromptInjectionResistanceEvaluator` — tests if output resists prompt injection
- [x] 9.2 Implement `PIILeakageDetectionEvaluator` — detects PII leakage in generated output
- [x] 9.3 Implement `JailbreakResistanceEvaluator` — tests resistance to jailbreak attempts

## 10. Programmatic Evaluators (Generic Layer)

- [x] 10.1 Create `evaluators/programmatic.py` with `PythonCodeEvaluator` — executes user-provided Python function safely against EvalInput
- [x] 10.2 Implement `CustomCallableEvaluator` — wraps an arbitrary async callable as an evaluator

## 11. Engine Enhancement

- [x] 11.1 Update `EvaluationEngine.run()` to separate deterministic evaluators from LLM-judge evaluators and run deterministic ones in parallel via `asyncio.gather`
- [x] 11.2 Add `tags` parameter to `EvaluationEngine.run()` for tag-filtered item selection
- [x] 11.3 Mark evaluation LLM calls with `metadata.purpose="evaluation"` in trace records for cost isolation

## 12. Model Changes & Migration

- [x] 12.1 Add `version: str`, `baseline_run_id: UUID | None`, `is_locked: bool`, `default_threshold: float | None` fields to `EvaluationDatasetModel`
- [x] 12.2 Add `assertions: list | None`, `tags: list | None` JSON fields to `EvaluationItemModel`
- [x] 12.3 Update Pydantic schemas (Create/Update/Read) for both Dataset and Item to include new fields
- [x] 12.4 Create Alembic migration adding new columns to `evaluation_datasets` and `evaluation_items` tables, chaining from current head
- [x] 12.5 Add `EVALUATION_REGRESSION` to `AlertType` StrEnum in `models/alert.py`

## 13. Dataset Service Updates

- [x] 13.1 Update `EvaluationDatasetService.create_dataset()` to accept `version`, `default_threshold` parameters
- [x] 13.2 Add `lock_dataset(dataset_id)` and `unlock_dataset(dataset_id)` methods
- [x] 13.3 Add `set_baseline_run(dataset_id, run_id)` method
- [x] 13.4 Update `add_items()` to accept and persist `assertions` and `tags` fields per item
- [x] 13.5 Enforce `is_locked` check: reject item add/update/delete on locked datasets with 409 Conflict
- [x] 13.6 Update `import_json()` and `export_json()` to include assertions and tags

## 14. Regression Service

- [x] 14.1 Create `services/regression_service.py` with `RegressionService` class
- [x] 14.2 Implement `compare_runs(baseline_run_id, candidate_run_id, threshold=0.05)` — computes per-metric deltas, identifies regressions
- [x] 14.3 Implement `compute_item_pass_fail(item, scores, dataset_default_threshold)` — evaluates assertions/thresholds for a single item
- [x] 14.4 Implement `run_regression(dataset_id, evaluators, tags, threshold, baseline_run_id)` — orchestrates eval run + comparison + pass/fail report
- [x] 14.5 Implement `_trigger_regression_alert(run_id, regressions)` — creates AlertEventModel when regression detected (integrates with 8.6 Alerting)

## 15. API New Endpoints

- [x] 15.1 Add `GET /api/evaluation/evaluators` endpoint — returns all registered evaluators with category, description, source_type; supports `?category=` filter
- [x] 15.2 Add `POST /api/evaluation/runs/compare` endpoint — accepts baseline_run_id + candidate_run_id, returns comparison report
- [x] 15.3 Add `POST /api/evaluation/regression/run` endpoint — accepts dataset_id + evaluators + optional tags/threshold/baseline_run_id, returns pass/fail report
- [x] 15.4 Update `POST /api/evaluation/runs` to accept optional `tags` parameter for tag-filtered runs
- [x] 15.5 Update `GET /api/evaluation/runs/{run_id}` to include pass/fail statistics (total_items, passed_items, failed_items, pass_rate)
- [x] 15.6 Add `PUT /api/evaluation/datasets/{id}/lock` and `PUT /api/evaluation/datasets/{id}/unlock` endpoints
- [x] 15.7 Add `PUT /api/evaluation/datasets/{id}/baseline` endpoint for setting baseline run

## 16. Alerting Integration

- [x] 16.1 Add `EvaluationRegressionSignalProvider` to `services/signal_provider.py` — reads evaluation run scores and computes regression metrics
- [x] 16.2 Update `NotificationDispatcher` message templates to include evaluation regression details (metric names, delta values, run IDs)

## 17. Tests — Evaluators

- [x] 17.1 Test all 9 deterministic format evaluators (exact_match, contains, contains_any, regex_match, is_json, format_check, bleu, rouge, f1) with pass/fail cases
- [x] 17.2 Test registry functions: get_evaluator, list_evaluators (with and without category filter), decorator registration
- [x] 17.3 Test JudgePromptTemplate construction and validation (scoring_scale types, rubric completeness)
- [x] 17.4 Test LLM-judge evaluators with mocked LLMService (toxicity, safety, instruction_following, coherence, fluency)
- [x] 17.5 Test citation evaluators with mocked LLMService (citation_relevance, source_attribution, groundedness, hallucination_detection)
- [x] 17.6 Test tool evaluators with mocked LLMService and tool_calls input
- [x] 17.7 Test multi-turn evaluators with conversation_history input
- [x] 17.8 Test safety evaluators with mocked LLMService (prompt_injection, pii_leakage, jailbreak)
- [x] 17.9 Test programmatic evaluators (python_code_eval with custom function, custom_callable)

## 18. Tests — Regression & API

- [x] 18.1 Test dataset versioning: create with version, lock/unlock, set baseline_run_id
- [x] 18.2 Test item assertions: create item with assertions, assertions override dataset default
- [x] 18.3 Test tag-filtered evaluation runs (only tagged items evaluated)
- [x] 18.4 Test pass/fail computation with assertions and thresholds
- [x] 18.5 Test run comparison API with regression detection (score drop > threshold)
- [x] 18.6 Test regression trigger API returns structured pass/fail report
- [x] 18.7 Test evaluation regression alert creation when scores drop
- [x] 18.8 Test evaluator listing API with and without category filter

## 19. Verification

- [x] 19.1 Run `ruff check src/hecate/ tests/` — zero errors
- [x] 19.2 Run `ruff format --check src/ tests/` — zero changes needed
- [x] 19.3 Run `mypy src/` — zero errors
- [x] 19.4 Run `python -m pytest tests/ -q` — all tests pass
