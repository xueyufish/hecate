## Purpose

Orchestrates evaluation-dataset-driven prompt self-optimization runs: validates run configuration, evaluates the base prompt as baseline, iterates mutation rounds where candidate prompt templates are rolled out against the real agent via prompt override, scores candidates with the evaluation engine, reflects on failure trajectories to propose new candidates, and enforces acceptance gates, budgets, and stop conditions — without ever mutating the production prompt or agent configuration.

## Requirements

### Requirement: Optimization run creation and config validation
The system SHALL expose an endpoint to create a prompt optimization run for a workspace-scoped prompt. The run config SHALL include: `prompt_id`, optional `base_version` (defaults to the prompt's current version), `agent_id` (the agent under test — the rollout vehicle whose invocation the candidate templates are injected into), `dataset_id` with a pinned `dataset_version`, a train/validation split ratio, an evaluator set with a designated `primary_metric`, an improvement threshold (`min_improvement`), a regression tolerance (`max_regression`), a budget preset (`light` / `medium` / `heavy`) or explicit caps (`max_rounds`, `max_llm_calls`), and an optional reflection model override. Creation SHALL be rejected with 404/400 when: the prompt, agent, or dataset version does not exist in the workspace, the split ratio is invalid, or the primary metric is not produced by the evaluator set. Creation SHALL be rejected with 409 when another active run already exists for the same prompt.

#### Scenario: Valid run creation
- **WHEN** a run is created with an existing prompt, a pinned dataset version, evaluators producing the primary metric, and the `medium` budget preset
- **THEN** the run is persisted with status `created`, the dataset version hash is recorded, and the endpoint returns the run id

#### Scenario: Primary metric not in evaluator set
- **WHEN** a run is created whose `primary_metric` is not produced by any evaluator in the configured evaluator set
- **THEN** creation is rejected with a validation error naming the mismatch

#### Scenario: Concurrent active run for the same prompt
- **WHEN** a run is created for a prompt that already has a run in status `created` or `running`
- **THEN** creation is rejected with 409

### Requirement: Feature flag gating
Prompt optimization endpoints and run execution SHALL be gated behind the `PROMPT_OPTIMIZATION_ENABLED` feature flag, default off. When the flag is off, run creation and mutation endpoints SHALL return 404, and no scheduled or background optimization work SHALL start.

#### Scenario: Flag off
- **WHEN** `PROMPT_OPTIMIZATION_ENABLED` is false and a client calls the run creation endpoint
- **THEN** the endpoint responds 404 and no run is persisted

#### Scenario: Flag on
- **WHEN** `PROMPT_OPTIMIZATION_ENABLED` is true
- **THEN** run creation, execution, and review endpoints operate normally

### Requirement: Run lifecycle
A run SHALL progress through statuses `created → running → awaiting_review → concluded`, with `failed` as a terminal error status. `awaiting_review` is entered when the loop stops (stop condition met) and at least the baseline evidence is complete. `concluded` is entered after the reviewer submits decisions (see review capability) or the run is explicitly cancelled. Internal failures mark the run `failed` with the error recorded; already-produced evidence remains queryable.

#### Scenario: Happy path lifecycle
- **WHEN** a run starts, executes its rounds, and reaches a stop condition
- **THEN** its status transitions `created → running → awaiting_review`, with all round evidence persisted

#### Scenario: Internal failure
- **WHEN** an unrecoverable error occurs mid-run (e.g., evaluation engine unavailable)
- **THEN** the run status becomes `failed`, the error is recorded, and evidence produced before the failure remains queryable

### Requirement: Baseline evaluation
Before the first mutation round, the system SHALL evaluate the base prompt version on the validation split of the pinned dataset version using the configured evaluator set, and record the baseline scores. All candidate comparisons and gate decisions SHALL be computed against this baseline.

#### Scenario: Baseline recorded
- **WHEN** a run enters its first mutation round
- **THEN** per-metric baseline scores on the validation split are persisted and exposed in the run detail

#### Scenario: No baseline without evaluation
- **WHEN** baseline evaluation fails (e.g., all items error)
- **THEN** the run SHALL NOT enter mutation rounds and transitions to `failed` with the evaluation error recorded

### Requirement: Candidate rollout via prompt override without production mutation
Each mutation round SHALL score candidate prompt templates by invoking the real agent under test with the candidate template injected as a per-invocation system prompt override. The production prompt, its versions, and the agent configuration SHALL NOT be mutated by any rollout. Rollout invocations SHALL be recorded as traces attributed to the run: trace metadata SHALL include the optimization run id and candidate id, and the original `prompt_id` / base `prompt_version`, so prompt analytics can distinguish optimization traffic from production traffic.

#### Scenario: Candidate scored on the real agent
- **WHEN** a round rolls out candidate templates against train-split items
- **THEN** each item's answer is generated by invoking the agent under test with the candidate template as the system prompt override, while the agent's tools, knowledge bases, and model configuration remain unchanged

#### Scenario: Production prompt untouched
- **WHEN** any rollout executes
- **THEN** the prompt's current version, its labels, and the agent configuration are identical before and after the rollout

#### Scenario: Rollout traces are attributed and separable
- **WHEN** a rollout invocation completes
- **THEN** the resulting trace metadata contains the optimization run id and candidate id

### Requirement: Reflection mutation with template integrity gate
The system SHALL generate candidate templates from rollout evidence: aggregated failure trajectories (query, expected, generated, evaluator reasoning) for the sampled items. Every generated candidate SHALL pass a template integrity gate before any rollout: the template MUST parse successfully in the prompt template engine and its extracted variable set MUST equal the base template's declared variable set. Candidates failing the gate SHALL be recorded as rejected mutations with the failure reason and SHALL NOT consume rollout budget.

#### Scenario: Valid candidate proceeds to rollout
- **WHEN** a generated candidate parses and its variable set matches the base template's declared variables
- **THEN** the candidate enters the round's rollout queue

#### Scenario: Invalid candidate blocked before rollout
- **WHEN** a generated candidate fails to parse or introduces/removes template variables
- **THEN** the candidate is persisted as a rejected mutation with the integrity error, and no rollout is executed for it

### Requirement: Acceptance gate
A candidate SHALL be accepted into the review pool only when, on the validation split: the primary metric improves over baseline by at least `min_improvement`, AND no deterministic-evaluator metric regresses beyond `max_regression`. LLM-judge metrics other than the primary SHALL be reported as advisory evidence and SHALL NOT gate acceptance. When the primary metric itself is judge-based, the comparison SHALL require the `min_improvement` margin to absorb judge noise. Every candidate's gate report (per-metric scores, deltas, per-check pass/fail) SHALL be persisted regardless of outcome.

#### Scenario: Candidate accepted
- **WHEN** a candidate's primary metric improves by ≥ `min_improvement` and no deterministic metric regresses beyond `max_regression`
- **THEN** the candidate is marked `pending_review` with its full gate report

#### Scenario: Candidate rejected by gate
- **WHEN** a candidate's primary metric fails to improve by `min_improvement` or a deterministic metric regresses beyond `max_regression`
- **THEN** the candidate is marked `gate_rejected` with the failing check identified in the report

#### Scenario: Judge metrics are advisory
- **WHEN** a non-primary LLM-judge metric regresses for a gate-passing candidate
- **THEN** the regression appears in the evidence report but the candidate is not blocked

### Requirement: Budget presets and hard caps
The system SHALL enforce budgets on every run: the presets `light` / `medium` / `heavy` map to defined caps on maximum rounds, maximum LLM/mutation calls, and maximum rollout items, and explicit caps in the run config override presets. When any cap is reached, the loop SHALL stop iterating and the run transitions to `awaiting_review` (or `concluded` if no candidates reached the review pool) with the stop reason recorded as `budget_exhausted`. Per-round usage (LLM calls, tokens, rollout count, duration, cost) SHALL be recorded and summed on the run, following the platform's lineage cost-accounting pattern.

#### Scenario: Budget preset applied
- **WHEN** a run is created with the `light` preset
- **THEN** the run's effective caps equal the `light` preset mapping and are visible on the run detail

#### Scenario: Hard cap stops the loop
- **WHEN** the maximum LLM call cap is reached mid-round
- **THEN** the loop stops, the stop reason `budget_exhausted` is recorded, and evidence produced so far remains queryable

#### Scenario: Per-round cost accounting
- **WHEN** a round completes
- **THEN** the round's usage record (calls, tokens, duration, cost) is persisted and reflected in the run's cumulative usage

### Requirement: Stop conditions
The loop SHALL stop and transition to `awaiting_review` when any of: the budget is exhausted (see budget requirement), the configured maximum round count completes, or the configured number of consecutive rounds (default 3) produces no gate-passing candidate (`no_progress`). The stop reason SHALL be recorded on the run. A user MAY explicitly cancel a running run; cancellation stops the loop at the next round boundary and records the stop reason `cancelled`.

#### Scenario: No-progress stop
- **WHEN** the configured number of consecutive rounds passes no candidate through the acceptance gate
- **THEN** the loop stops with reason `no_progress` and the run transitions to `awaiting_review`

#### Scenario: Explicit cancel
- **WHEN** a user cancels a running run
- **THEN** the loop stops at the next round boundary with reason `cancelled` and evidence remains queryable

### Requirement: Reproducibility via pinned inputs
A run SHALL record, at creation: the pinned dataset version hash, the evaluator configuration, the strategy name and parameters, the effective budget caps, and the model configuration used for reflection. Post-run changes to the live dataset or evaluator defaults SHALL NOT affect a running or completed run.

#### Scenario: Dataset drift does not affect the run
- **WHEN** the live dataset is modified after a run's dataset version was pinned
- **THEN** the run continues (and its records show) the pinned version's items, not the modified live items

#### Scenario: Full config lineage
- **WHEN** a completed run's detail is queried
- **THEN** the response includes the dataset version hash, evaluator config, strategy parameters, budget caps, and reflection model used

### Requirement: Workspace isolation
Runs, candidates, and their evidence SHALL be workspace-scoped. A run SHALL only reference prompts, agents, datasets, and evaluator configurations resolvable within the run creator's workspace, and all run and candidate queries SHALL be scoped to the caller's workspace.

#### Scenario: Cross-workspace reference rejected
- **WHEN** a run is created referencing a prompt that exists only in another workspace
- **THEN** creation is rejected with 404

#### Scenario: Query scoping
- **WHEN** a run or candidate list is queried
- **THEN** only records belonging to the caller's workspace are returned
