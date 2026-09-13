## Purpose

Behavior contract for the runtime intent recognition engine: a layered recognition pipeline (atomic → workflow → session, plus optional domain label), a fast-path classifier stack with LLM fallback, decision caching, published-evidence-only consumption, decision events, and session-intent persistence. Consumed by the multi-agent controller and the workflow intent routing mode.

## ADDED Requirements

### Requirement: Layered recognition result

The recognition engine SHALL produce a layered result for each recognized user turn: an atomic intent (label, confidence, decision source), a workflow intent (label and active flag derived from multi-turn accumulation), a session intent (overall goal state), and an optional domain label (present only when domain classification is configured). Each level SHALL be derived from the previous level and the session context. The result SHALL expose the decision source of the atomic level as one of `cache`, `pattern`, `few_shot`, `llm`, or `fallback`.

#### Scenario: Single-turn atomic classification

- **WHEN** a user turn "show me the billing report" is recognized with evidence containing a "billing" category
- **THEN** the result SHALL carry an atomic intent labeled from the evidence categories with a confidence value and a decision source indicating which fast path or fallback produced it

#### Scenario: Multi-turn accumulation produces workflow intent

- **WHEN** consecutive turns in one session express steps of one multi-step task ("generate the quarterly report", "now send it to stakeholders")
- **THEN** the workflow-intent level SHALL become active with a task-level label, while each turn still receives its own atomic intent

#### Scenario: Session intent persists across turns

- **WHEN** a session has accumulated a session-level goal and a later unrelated turn arrives that does not signal a goal shift
- **THEN** the session intent SHALL remain unchanged and the atomic result SHALL be classified in the context of that goal

#### Scenario: Domain label only when configured

- **WHEN** the recognition configuration enables domain classification
- **THEN** the result SHALL include a domain label; WHEN domain classification is not configured, the result SHALL omit the domain level entirely

### Requirement: Fast-path classifier stack with LLM fallback

The engine SHALL evaluate the atomic level through an ordered fast-path stack: decision cache first, then package-provided patterns/keywords, then few-shot classification over package evidence, then LLM classification as the final fallback. Each fast path SHALL skip the more expensive steps after it. The engine SHALL support configuring the recognition model separately from the business/workflow model.

#### Scenario: Cache hit skips all other paths

- **WHEN** an utterance fingerprint, evidence version, and context fingerprint match a cached decision
- **THEN** the engine SHALL return the cached result without invoking pattern matching, few-shot assembly, or the LLM

#### Scenario: Pattern match skips evidence and LLM

- **WHEN** a package-provided pattern matches the utterance and the cache missed
- **THEN** the engine SHALL return the pattern's label without invoking few-shot classification or the LLM

#### Scenario: Few-shot evidence precedes LLM fallback

- **WHEN** the cache and patterns miss but the referenced package has published evidence
- **THEN** the engine SHALL classify using the package categories and sample utterances as few-shot evidence before falling back to a plain LLM classification

#### Scenario: Separate recognition model honored

- **WHEN** the recognition configuration names a dedicated recognition model
- **THEN** LLM-backed classification SHALL use that model, not the workflow execution model

### Requirement: Intent decision cache

The engine SHALL cache atomic-level decisions keyed by a normalized utterance fingerprint, the evidence version, and a context fingerprint. Cache entries SHALL expire after a configurable TTL. A cache hit SHALL be observable on the recognition result and on the emitted decision event. Changing the evidence version SHALL make prior cache entries ineligible.

#### Scenario: Repeated utterance in same context hits cache

- **WHEN** the same normalized utterance is recognized twice within the TTL with the same evidence version and context fingerprint
- **THEN** the second recognition SHALL report decision source `cache` and complete without an LLM call

#### Scenario: Context change misses cache

- **WHEN** the same utterance is recognized with a different context fingerprint (for example, a different accumulated session state)
- **THEN** the engine SHALL NOT reuse the cached decision

#### Scenario: Evidence version change invalidates cache

- **WHEN** the referenced intent package publishes a new version between two identical recognitions
- **THEN** the second recognition SHALL NOT reuse the cached decision from the previous evidence version

### Requirement: Evidence comes only from published intent packages

The engine SHALL consume few-shot evidence exclusively from published intent package versions, resolved through an injected evidence port. Draft or unpublished categories and samples SHALL never influence runtime recognition. Graph validation SHALL reject recognition configurations that reference a non-existent package or an unresolvable version. If the evidence port fails at recognition time, the engine SHALL fall back to the LLM path and mark the result as lacking evidence.

#### Scenario: Draft samples do not affect runtime

- **WHEN** a sample utterance is added to a package draft after its current version was published
- **THEN** runtime recognition SHALL continue to use only the published version's evidence until the draft is published

#### Scenario: Unknown package reference rejected at validation

- **WHEN** a recognition configuration references a package id that does not exist in the workspace, or a version pin that is not published
- **THEN** graph validation SHALL fail with an error identifying the unresolvable reference

#### Scenario: Evidence read failure degrades gracefully

- **WHEN** the evidence port raises at recognition time
- **THEN** the engine SHALL return an LLM-classified result with evidence marked unavailable, and the decision event SHALL record the degradation instead of failing the turn

### Requirement: Recognition decisions are persisted as events

Every recognition SHALL emit an additive event into the EventStore capturing: the layered result, the decision source per level, cache-hit flag, the evidence reference (package id and version), and latency. The event SHALL NOT embed the few-shot evidence payloads themselves.

#### Scenario: Decision event emitted per recognition

- **WHEN** a turn is recognized by any consumer (controller or intent routing mode)
- **THEN** exactly one recognition event SHALL be appended containing the layered result, decision source, cache-hit flag, and evidence version reference

#### Scenario: Event excludes evidence payloads

- **WHEN** a recognition used a package with hundreds of sample utterances
- **THEN** the emitted event SHALL contain the evidence reference but none of the sample payload content

### Requirement: Session intent is durable state

Session intent SHALL be persisted in the session state and restored with it. A detected explicit goal shift SHALL update the persisted session intent; otherwise it SHALL be carried forward. Restoring a session SHALL restore the prior session intent without re-deriving it from scratch.

#### Scenario: Session intent survives turn boundary and restore

- **WHEN** a session accumulates a session-level goal, ends the turn, and the session is later restored
- **THEN** the restored session state SHALL contain the same session intent without requiring re-recognition of past turns

#### Scenario: Explicit goal shift updates session intent

- **WHEN** a turn is recognized as an explicit shift of the overall goal
- **THEN** the persisted session intent SHALL be updated to the new goal and subsequent recognitions SHALL use it

### Requirement: Policy-gated intents bypass recognition

An intent category MAY be flagged as policy-gated. Executing a policy-gated route SHALL require the deterministic approval/policy path regardless of recognition confidence or decision source. The recognition engine SHALL NOT override a policy gate through any recognition output, including cached or pattern-matched decisions.

#### Scenario: Policy gate applies to cached decisions

- **WHEN** a category flagged as policy-gated is matched by a cached decision with high confidence
- **THEN** the routed execution SHALL still be subject to the deterministic approval path before performing the gated action

#### Scenario: Recognition cannot promote a gated route

- **WHEN** recognition output suggests a policy-gated category
- **THEN** the engine SHALL mark the decision as gated and SHALL NOT emit a result that routes execution past the policy check
