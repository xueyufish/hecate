## Purpose

Adds the 7th multi-agent collaboration pattern (Dynamic Orchestration) as a graph-native runtime-emitted sub-graph capability. A coordinator node transforms a goal plus an agent roster into a runtime task DAG, dispatches workers in isolated child sessions through the existing Pregel runtime, and folds their outputs through synthesis — building on the log-as-truth substrate (ADR-030) so the plan itself is observable, replayable, and auditable.

## Requirements

### Requirement: COORDINATOR NodeType exists
The system SHALL define a `COORDINATOR` value in the `NodeType` StrEnum in `engine/types.py`. The value MUST be lowercase string `"coordinator"` and SHALL be a first-class dispatch branch in `PregelRuntime._execute_inner`, alongside `FAN_IN` and `MERGE`.

#### Scenario: NodeType value present in enum
- **WHEN** `from hecate.engine.types import NodeType` is executed
- **THEN** `NodeType.COORDINATOR` SHALL be accessible with `.value == "coordinator"`

#### Scenario: Coordinator node dispatches via worker pool
- **WHEN** a compiled graph has a node with `type == NodeType.COORDINATOR`
- **THEN** `PregelRuntime._execute_inner` SHALL route that node through `CoordinatorWorker` instead of the default worker pool

### Requirement: TaskDAG public contract
The system SHALL define `TaskDAG` as a Pydantic model in `engine/dynamic_types.py` with the following fields: `goal: str` (non-empty), `tasks: list[TaskNode]`, `dependencies: dict[str, list[str]]` mapping task id to upstream task ids, `synthesis_prompt: str | None`, `budgets: OrchestrationBudgets`, `verify: VerifyConfig | None`. `TaskNode` SHALL have `id: str`, `agent_id: str`, `inputs: dict[str, str]` (mapping local input name to upstream task id + output name), `expected_output: str`, `on_failure: Literal["continue","stop","replan"] = "stop"`.

#### Scenario: Valid TaskDAG round-trips through Pydantic
- **WHEN** a TaskDAG with 3 tasks and 2 dependencies is constructed and serialized
- **THEN** it SHALL pass Pydantic validation and deserialize to an equivalent object

#### Scenario: Empty goal rejected at validation
- **WHEN** a TaskDAG with `goal = ""` is constructed
- **THEN** Pydantic SHALL raise `ValidationError` rejecting the empty string

#### Scenario: Self-dependency rejected at validation
- **WHEN** a TaskDAG declares `dependencies["task_a"] = ["task_a"]`
- **THEN** validation SHALL reject with a cycle error

### Requirement: Fail-closed pre-dispatch validation
The system SHALL provide a `validate_task_requirements(dag: TaskDAG, roster: list[AgentRequirement]) -> ValidationReport` function exported from `engine/orchestrator_validator.py`. The function MUST reject the orchestration before any worker dispatches when any of the following hold: (a) the DAG contains a cycle, an unreferenced node, or a dangling dependency reference; (b) `tasks[].agent_id` is not present in the roster; (c) `tasks[].agent_id` requires a tool or knowledge base that no roster agent exposes; (d) `tasks[].inputs` references an output name that the upstream task does not declare. The function MUST be callable both at coordinator worker construction time and from the graph design canvas at save time.

#### Scenario: DAG with cycle rejected
- **WHEN** `validate_task_requirements` is called with a DAG where `dependencies["b"] = ["b"]`
- **THEN** it SHALL return a `ValidationReport` with `is_valid = False` and a `cycle` error tagged to task `b`

#### Scenario: Task requiring absent capability rejected
- **WHEN** a task references agent `legal-expert` and the roster contains only `general-purpose`
- **THEN** `validate_task_requirements` SHALL return `is_valid = False` with an `unsatisfiable_requirement` error

#### Scenario: All requirements satisfiable
- **WHEN** every task's `agent_id` and capability requirements are satisfied by the roster
- **THEN** `validate_task_requirements` SHALL return `is_valid = True` with no errors

### Requirement: CoordinatorWorker executes the orchestration cycle
The system SHALL provide `CoordinatorWorker` in `engine/workers/coordinator_worker.py`. On each superstep where the COORDINATOR node is scheduled, it SHALL: (1) call the planner model with the goal + roster + current `_ledger` channel to obtain a candidate `TaskDAG` revision; (2) call `validate_task_requirements` against the current roster; (3) write the validated TaskDAG to the `_plan` channel; (4) emit an `ORCHESTRATOR_DECISION` event with payload `{plan_revision, dag, reasoning}`; (5) invoke the executor template to compile the TaskDAG into a sub-GraphConfig; (6) execute the sub-graph in a fresh `child_session_id` with a fresh `ChannelManager`; (7) on sub-graph completion, run the evaluator model to produce an `ORCHESTRATOR_EVALUATION` event with typed blocker payload. The coordinator MUST abort and raise an error if validation fails at step 2.

#### Scenario: Valid revision proceeds
- **WHEN** the planner returns a TaskDAG that passes `validate_task_requirements`
- **THEN** the coordinator SHALL write it to `_plan`, emit `ORCHESTRATOR_DECISION`, compile and run the sub-graph, and emit `ORCHESTRATOR_EVALUATION`

#### Scenario: Invalid revision rejected
- **WHEN** the planner returns a TaskDAG where one task references an agent absent from the roster
- **THEN** the coordinator SHALL NOT execute the sub-graph and SHALL emit an `ORCHESTRATOR_DECISION` event with `dag = None` and a `verification_failed` reasoning

### Requirement: Iterative loop with stall detection and replan-with-carryover
The system SHALL iterate the coordinator outer loop until the evaluator emits `verdict = "satisfied"` or one of the configured budgets is exhausted. On each iteration the coordinator SHALL emit a new `ORCHESTRATOR_DECISION` event rather than rewriting the previous one (history-preserving). On replan, the new TaskDAG MAY reference outputs of previously-completed tasks by their stable `task_id` (replan-with-carryover) — the executor MUST hydrate those inputs from the `_ledger` channel rather than re-running the producing tasks. The coordinator MUST maintain a `stall_counter` that increments when consecutive `ORCHESTRATOR_EVALUATION` events return `verdict = "stalled"` and resets when `verdict != "stalled"`. When `stall_counter` exceeds `budgets.stall_limit`, the coordinator SHALL emit an `ORCHESTRATOR_DECISION` with `dag = None` and `reasoning = "stall_cap_exceeded"` and SHALL NOT execute further iterations.

#### Scenario: Verdict satisfied terminates loop
- **WHEN** an `ORCHESTRATOR_EVALUATION` event is emitted with `verdict = "satisfied"`
- **THEN** the coordinator SHALL mark the orchestration complete and return the synthesis output

#### Scenario: Stall counter increments and triggers stop
- **WHEN** the evaluator emits `verdict = "stalled"` three times in a row and `stall_limit = 2`
- **THEN** the coordinator SHALL emit the final `ORCHESTRATOR_DECISION` with `reasoning = "stall_cap_exceeded"` and SHALL NOT execute any further sub-graphs

#### Scenario: Replan references prior task output
- **WHEN** iteration 2's TaskDAG declares an input `summary` from `task_id = "research"` and `research` completed in iteration 1
- **THEN** the executor SHALL read the value from the `_ledger` channel under the `research` task's output key and SHALL NOT re-execute the research task

### Requirement: Three-axis budget enforcement with additive stop_reason
The system SHALL enforce three independent budgets configured in `OrchestrationBudgets`: `max_iterations: int = 5`, `max_total_tasks: int = 6` (range 1–50), `max_concurrent: int = 3` (range 1–4), `stall_limit: int = 2`, and `token_budget: int | None` (checked at task boundary). When any budget is exhausted mid-run, the system SHALL emit an `ORCHESTRATOR_EVALUATION` event with an additive `stop_reason` field — one of `"token_capped"`, `"turn_capped"`, `"loop_capped"`, `"stall_capped"` — in addition to the normal `verdict`. The status enum SHALL NOT be extended. When a `stop_reason` is set, the `ORCHESTRATOR_EVALUATION` payload MUST include the model-visible guidance string `"reuse it, retry tighter, or raise budget"` so the next iteration's planner does not mistake a capped partial result for a clean completion.

#### Scenario: Token budget exhaustion emits stop_reason
- **WHEN** cumulative token usage exceeds `token_budget` at a task boundary
- **THEN** the next `ORCHESTRATOR_EVALUATION` SHALL carry `stop_reason = "token_capped"` and the guidance string

#### Scenario: Concurrent cap truncates TaskDAG
- **WHEN** a TaskDAG declares 5 parallel tasks at the same dependency level and `max_concurrent = 3`
- **THEN** the executor SHALL dispatch only 3 concurrently and SHALL defer the remaining 2 to a later superstep

#### Scenario: Total task cap counted per orchestration
- **WHEN** cumulative dispatched tasks reach `max_total_tasks`
- **THEN** the executor SHALL refuse further task dispatches and emit `stop_reason = "turn_capped"`

### Requirement: Hard isolation between parent and sub-graph sessions
The system SHALL execute each orchestrated sub-graph in an isolated child session that satisfies all five isolation properties: (1) the sub-graph uses a distinct `session_id` from the parent and a fresh `ChannelManager`; (2) long-term memory writes from the sub-graph MUST NOT propagate to the parent's `thread_id`; (3) the sub-graph's `ChannelManager` SHALL register only channels explicitly listed in the executor's `channel_mapping.input` — parent channels not declared MUST be unreadable; (4) after sub-graph completion, the executor SHALL write back only the values of channels listed in `channel_mapping.output`; the sub-graph's `messages` channel MUST NOT be written back to the parent; (5) failure of a sub-task MUST be determined by `WorkerResult.error` / status contract only — the system MUST NOT parse sub-agent output text to determine success or failure.

#### Scenario: Sub-graph cannot read undeclared parent channel
- **WHEN** the executor compiles a TaskDAG and the parent has a channel `secret_context` not declared in `channel_mapping.input`
- **THEN** the sub-graph's `ChannelManager.read("secret_context")` SHALL raise `KeyError` and SHALL NOT expose the parent value

#### Scenario: Sub-agent output text does not determine failure
- **WHEN** a sub-task worker returns `WorkerResult.error = None` but the sub-agent's textual response contains the phrase "I cannot complete this task"
- **THEN** the executor SHALL treat the task as completed and SHALL NOT mark it failed based on text parsing

#### Scenario: Sub-session memory is isolated from parent thread
- **WHEN** a sub-task writes to long-term memory during execution
- **THEN** the write MUST be keyed by the child's session id and MUST NOT appear in the parent's thread-scoped memory store

### Requirement: Synthesis supports deterministic transforms before LLM
The system SHALL allow a TaskDAG to declare a `synthesis_transform` (string expression) and an optional `synthesis_prompt`. When `synthesis_transform` is provided, the executor SHALL evaluate it against the synthesized context (a dict of `task_id → expected_output`) using the same expression evaluator as `VARIABLE_SET` nodes, before invoking the LLM with `synthesis_prompt`. The transform output SHALL be written to a `_synthesis_buffer` channel visible only to the synthesis node.

#### Scenario: Deterministic transform runs before LLM
- **WHEN** a TaskDAG declares `synthesis_transform = "join | sort | dedupe"` and three completed task outputs
- **THEN** the executor SHALL evaluate the transform against the three outputs and feed the result as context to the synthesis LLM call

#### Scenario: LLM-only synthesis fallback
- **WHEN** a TaskDAG declares `synthesis_transform = None` and `synthesis_prompt = "Synthesize the findings"`
- **THEN** the executor SHALL skip the transform step and invoke the synthesis LLM with the raw per-task outputs

### Requirement: Optional per-task verification hook
The system SHALL allow each `TaskNode` to declare a `verify` field referencing another roster agent id and an optional verification prompt. When set, after the task's primary worker returns `WorkerResult.error = None`, the executor SHALL invoke the verifier agent with the task output and the verification prompt; the verifier's response MUST be written to the task's ledger entry as `verified: bool`. If `verify` is `None`, the task is considered verified by on `error = None`.

#### Scenario: Verification passes on clean output
- **WHEN** a task declares `verify = {verifier_id: "judge", " prompt": "..."}` and the verifier agent returns "PASS"
- **THEN** the task's ledger entry SHALL record `verified = true`

#### Scenario: Verification fails on bad output
- **WHEN** a task declares a verifier and the verifier returns "FAIL"
- **THEN** the task's ledger entry SHALL record `verified = false` and the next `ORCHESTRATOR_EVALUATION` SHALL emit `verdict = "stalled"` for that task

### Requirement: New EventTypes for orchestration decisions
The system SHALL extend `EventType` in `engine/eventstore.py` additively with two values: `ORCHESTRATOR_DECISION` and `ORCHESTRATOR_EVALUATION`. The `LogPolicy.should_log_channel` function SHALL NOT exclude these event types. Each `ORCHESTRATOR_DECISION` event payload MUST include `plan_revision: int` (monotonically increasing per orchestration), `dag: dict | None` (the TaskDAG or `None` for replan-cap-terminated iterations), and `reasoning: str`. Each `ORCHESTRATOR_EVALUATION` event payload MUST include `verdict: Literal["satisfied", "needs_user_input", "missing_evidence", "run_failed", "external_wait", "goal_not_met_yet", "stalled"]`, `blocker: str | None`, and optional `stop_reason` and `guidance_string`.

#### Scenario: ORCHESTRATOR_DECISION carries plan_revision
- **WHEN** iteration 2 of an orchestration emits a new `ORCHESTRATOR_DECISION`
- **THEN** its `plan_revision` SHALL be `2` and the previous event's `plan_revision` SHALL remain `1`

#### Scenario: Unknown verdict falls back gracefully
- **WHEN** the evaluator returns a verdict string not in the canonical set
- **THEN** the event SHALL be recorded with `verdict = "stalled"` and `blocker` containing the original string

### Requirement: Benefit-based delegation rubric
The system SHALL embed a `benefit_based_delegation_rubric` in the planner system prompt that instructs the planner to dispatch subagents only when the parallel latency, specialist capability, or context-isolation benefit clearly exceeds startup, duplicate-discovery, synthesis, state-conflict, and side-effect costs; output dependency between parallel tasks and overlapping mutable state SHALL be hard vetoes against parallel dispatch. The rubric text SHALL be exposed via a public constant and a `tests/test_coordinator_prompt.py` snapshot test SHALL pin the rubric string byte-for-byte. A second test SHALL fail if the planner system prompt is modified without re-running the snapshot.

#### Scenario: Rubric present in planner prompt
- **WHEN** `CoordinatorWorker` constructs the planner system prompt
- **THEN** it SHALL contain the canonical `benefit_based_delegation_rubric` text

#### Scenario: Prompt drift detected by snapshot test
- **WHEN** the rubric text is changed without updating the snapshot
- **THEN** `pytest tests/test_coordinator_prompt.py` SHALL fail with a diff against the recorded snapshot

### Requirement: Model separation for planner, evaluator, and workers
The system SHALL accept three independent model configurations on the coordinator node: `planner_model` (default the planner's default), `evaluator_model` (default a non-thinking small model), and per-task `model` overrides from the roster. The coordinator MUST route the planner LLM call to `planner_model`, the evaluator LLM call to `evaluator_model`, and each task's primary worker LLM call to that task's roster agent's configured model.

#### Scenario: Three distinct model invocations
- **WHEN** `planner_model = "planner-x"`, `evaluator_model = "evaluator-y"`, and a task's agent uses `model = "worker-z"`
- **THEN** an orchestration iteration SHALL produce LLM calls from `planner-x`, the per-task `worker-z`, and `evaluator-y` in that order