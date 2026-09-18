# grounding-scoring Specification

## Purpose
Runtime grounding scoring for tool-grounded agent responses: after a response is produced, factual claims are scored against their evidence (cited chunks or fallback-retrieved context) and the verdicts are recorded as audit events. The layer is observational — it never blocks, rewrites, or reorders delivery — and manufactures the measurement base a later disposition stage calibrates against.

## Requirements

### Requirement: Grounding scoring of factual claims

When grounding scoring is enabled for a node, the system SHALL score each factual-eligible sentence of an LLM response — the same sentence inventory used by the citation ratio signal — against its evidence, producing a per-sentence verdict of `supported`, `contradicted`, or `unverifiable` with a confidence value. Responses with no factual-eligible sentences SHALL NOT be scored. Sentences are scored independently; a sentence with multiple cited chunks SHALL be scored against each cited chunk and the per-sentence verdict SHALL reflect the best-supported outcome.

#### Scenario: Cited claim scored against its cited chunks

- **WHEN** a factual sentence cites one or more chunk markers that resolve in the session registry
- **THEN** each cited chunk's text SHALL be used as evidence for that sentence, and the sentence SHALL receive a verdict and confidence derived from those chunks

#### Scenario: Response without factual sentences

- **WHEN** a response has no factual-eligible sentences under the shared sentence heuristic
- **THEN** no scoring SHALL be performed for that response

### Requirement: Evidence acquisition via citation lookup and fallback retrieval

Evidence for a factual sentence SHALL be acquired in order: first, the text of chunks cited by that sentence, resolved through the session chunk registry; second, for sentences with no resolvable citation, a fallback knowledge retrieval against the policy-configured fallback knowledge bases using the sentence as the query. When no fallback knowledge bases are configured, or fallback retrieval returns no results, the sentence SHALL be scored `unverifiable`. Evidence acquired via fallback SHALL be distinguishable from cited-chunk evidence in the recorded verdict.

#### Scenario: Uncited claim gets fallback evidence

- **WHEN** a factual sentence cites no resolvable marker and the node has fallback knowledge bases configured
- **THEN** the system SHALL retrieve against those knowledge bases using the sentence text, and score the sentence against the retrieved context if any is returned

#### Scenario: No fallback configured

- **WHEN** a factual sentence cites no resolvable marker and no fallback knowledge bases are configured (or retrieval returns nothing)
- **THEN** the sentence SHALL be scored `unverifiable` without a model or endpoint call

### Requirement: Three-way verdict semantics

A `contradicted` verdict SHALL require the scoring backend to report explicit evidence disagreement; low confidence or absence of support MUST NOT be recorded as `contradicted`. Confidence values SHALL NOT be folded into the verdict classification: a weakly supported sentence is `supported` with low confidence, not `contradicted` or `unverifiable`.

#### Scenario: Explicit contradiction

- **WHEN** the scoring backend reports that the evidence disagrees with the claim
- **THEN** the sentence SHALL be recorded as `contradicted`

#### Scenario: Weak support is not contradiction

- **WHEN** the scoring backend reports weak or ambiguous support
- **THEN** the sentence SHALL be recorded as `supported` with low confidence, never as `contradicted`

### Requirement: Scoring backends

Scoring SHALL be delegated to a pluggable backend selected per node policy. A built-in LLM judge backend SHALL invoke the platform model invocation path with a contract that yields the three-way verdict and confidence per (claim, evidence) pair. An HTTP NLI backend SHALL call an operator-configured HTTP endpoint with a `(claim, evidence)` payload and map the response to the same verdict contract, enabling self-hosted entailment models (e.g. MiniCheck-class) without embedding a model runtime in the platform. Backend invocation failures SHALL degrade to no-verdict for the affected sentences — recorded in the audit event with a degradation marker — and SHALL NOT fail the invocation or alter the response.

#### Scenario: LLM judge backend selected

- **WHEN** a node's policy selects the LLM judge backend and a response is scored
- **THEN** verdicts SHALL be produced via the platform model invocation path with the three-way contract

#### Scenario: HTTP NLI backend selected

- **WHEN** a node's policy selects the HTTP NLI backend with a configured endpoint
- **THEN** each (claim, evidence) pair SHALL be sent to that endpoint and the response mapped to the verdict contract

#### Scenario: Backend failure degrades without blocking

- **WHEN** the selected backend errors or times out during scoring
- **THEN** the invocation SHALL complete normally, affected sentences SHALL be recorded without verdicts plus a degradation marker, and a warning SHALL be logged

### Requirement: Observation-only behavior contract

Scoring SHALL NOT block, rewrite, reorder, or withhold the response, and SHALL NOT change user-visible content. In non-streaming execution the scoring step MAY add latency to the invocation before the result is returned; in streaming execution scoring SHALL run after delivery, and streamed tokens SHALL be unaffected. A scored-contradicted response SHALL still be delivered unchanged in this stage.

#### Scenario: Contradicted response delivered unchanged

- **WHEN** scoring yields one or more `contradicted` verdicts
- **THEN** the response SHALL be delivered exactly as generated, with no replacement message, annotation of content, or suppression

#### Scenario: Streaming delivery unaffected

- **WHEN** a node with scoring enabled streams a response
- **THEN** token delivery SHALL not be buffered or delayed by scoring, and scoring results SHALL appear only in the audit event

### Requirement: Scoring trigger and budget control

Scoring SHALL run only when the node policy's trigger condition is met: `on_uncited` (default — score only when the citation ratio signal reports at least one uncited factual sentence), `always`, or a configured sample rate. When the trigger is not met, scoring SHALL be skipped entirely with no backend calls and no scoring event.

#### Scenario: Zero uncited ratio with on_uncited trigger

- **WHEN** a response's factual sentences all carry citations and the trigger is `on_uncited`
- **THEN** no scoring SHALL run and no scoring event SHALL be emitted

#### Scenario: Always trigger

- **WHEN** the trigger is `always` and the policy is enabled
- **THEN** every response with at least one factual sentence SHALL be scored

### Requirement: Grounding score audit event with shadow disposition

Each scored response SHALL append one additive audit event recording: per-sentence verdicts with confidence and evidence references (cited marker, fallback source, or none), degradation markers, response-level aggregates (supported / contradicted / unverifiable counts and ratios), and a `would_block` shadow disposition computed against preset disposition thresholds. The shadow disposition SHALL have no user-visible or behavioral effect; it exists to calibrate a future disposition stage against real traffic. Event emission failure SHALL be logged and SHALL NOT fail the invocation.

#### Scenario: Scored response emits event

- **WHEN** a response completes scoring
- **THEN** one audit event SHALL be recorded containing the per-sentence verdicts, evidence references, aggregates, and the shadow disposition

#### Scenario: Shadow threshold crossed

- **WHEN** the response aggregates cross the preset disposition thresholds
- **THEN** the event's shadow disposition SHALL be marked accordingly, with no change to delivery

#### Scenario: Emission failure

- **WHEN** the audit event cannot be appended
- **THEN** a warning SHALL be logged and the invocation SHALL complete normally

### Requirement: Evidence resolution independence from projection state

Evidence lookup for scoring SHALL resolve through the session chunk registry, which is independent of projection state. Window selection, offloading, compression, and truncation between tool result ingestion and response scoring SHALL NOT invalidate evidence: chunks shortened by truncation SHALL be scored against their recorded partial text with the truncation state preserved.

#### Scenario: Offloaded chunk still usable as evidence

- **WHEN** a cited chunk's message has been offloaded in the projection but is registered in the session registry
- **THEN** scoring SHALL resolve the chunk text from the registry and score against it

#### Scenario: Truncated chunk scored as partial evidence

- **WHEN** a cited chunk was truncated at ingestion
- **THEN** scoring SHALL use the truncated text and the recorded evidence reference SHALL preserve the truncated state
