## Purpose

Defines the pluggable context processor chain that replaces the hardcoded LLMWorker context pipeline: an ordered, budget-satisfied projection pipeline with atomic message grouping, provider-anchored token estimation, scoped policy resolution (node > agent > model-capability > global), first-party processors (budget warn hint, tool-result truncation, round window, offload/recall, compression, KV-cache awareness, runtime hints, controlled termination), an executor-owned failure policy, and degradation observability. The chain projects conversation history per LLM invocation without mutating channel state or the event log.

## Requirements

### Requirement: Processor chain executes context projection before LLM invocation

When a processor chain is configured for an invocation, LLMWorker SHALL delegate all context projection to the chain instead of the built-in five-step pipeline. Processors SHALL run in declared order. The chain SHALL stop early (satisfied predicate) once the projected context fits the resolved token budget. Each processor SHALL return a result carrying its degradation level, tokens saved, and processor-specific metadata; the chain SHALL aggregate these into a single budget snapshot per invocation. The snapshot SHALL be emitted only when at least one degradation level fired. Projection SHALL be non-destructive: the chain output is a temporary message list for the current LLM invocation only.

#### Scenario: Under budget — processors skip, no snapshot

- **WHEN** the estimated token count of the incoming context is within the resolved budget
- **THEN** degradation processors SHALL skip without transforming the message list
- **AND** no budget snapshot SHALL be emitted (no degradation level fired)

#### Scenario: Over budget — ordered application until satisfied

- **WHEN** the estimated token count exceeds the resolved budget
- **THEN** processors SHALL apply in declared order, and the chain SHALL stop at the first processor after which the projected context fits the budget

#### Scenario: Degradation metadata aggregated into one snapshot

- **WHEN** the warn hint and the window selection both fire during one invocation
- **THEN** exactly one budget snapshot SHALL be emitted with `levels` in execution order (e.g. `["warn", "drop"]`), plus tokens/messages before and after

#### Scenario: Streaming path uses the same chain

- **WHEN** LLMWorker runs `execute_stream` with a configured chain
- **THEN** the same chain projection SHALL be applied before the LLM call
- **AND** streamed tokens SHALL correspond to the projected messages, not the full history

### Requirement: Atomic unit grouping

Before processors run, the chain SHALL group tool-call-linked messages into indivisible units: an assistant message carrying `tool_calls` together with all tool result messages linked by `tool_call_id`. No processor SHALL split, partially drop, or reorder the members of a unit. The chain SHALL validate pairing before projection: a tool result without its call, or a call without its result, SHALL be normalized (synthetic output attached or the incomplete unit dropped whole) so the projected context is provider-acceptable.

#### Scenario: Window boundary cannot split a unit

- **WHEN** a selection boundary falls between an assistant `tool_calls` message and its tool result
- **THEN** the boundary SHALL move to the unit boundary so both messages are kept or dropped together

#### Scenario: Dangling tool result normalized

- **WHEN** the incoming history contains a tool result whose call is missing
- **THEN** the chain SHALL normalize the history (drop the dangling result or attach a synthetic call output) before processors operate on it

### Requirement: Token estimation via provider anchor with heuristic fallback

The chain SHALL estimate token counts through a token estimator abstraction that prefers provider-reported usage anchored to the last LLM response (plus incremental deltas for appended messages); when no anchor exists, estimates come from the legacy `ContextEngine`'s estimator if one is present (legacy parity), and from a character-based heuristic (approximately 4 characters per token) otherwise. All processor thresholds SHALL consume estimates from this estimator.

#### Scenario: Provider anchor available

- **WHEN** the session has a provider usage anchor from the last LLM response
- **THEN** estimates SHALL be derived from the anchored usage plus deltas of newly appended messages

#### Scenario: No anchor — legacy engine or heuristic fallback

- **WHEN** no provider anchor exists (e.g. first call of a session)
- **THEN** estimates SHALL use the legacy `ContextEngine`'s estimator when an engine is present
- **AND** otherwise estimates SHALL use the character-based heuristic

### Requirement: Budget warn hint injection

When the estimated usage crosses the configured warn threshold (a fraction of the budget), the chain SHALL inject a model-visible hint message into the projected context informing the model of current usage and the approaching budget. The hint SHALL be appended to the context (persistent for subsequent turns), SHALL NOT modify the system prompt, and SHALL be injected at most once per threshold crossing (no repetition while usage remains elevated).

#### Scenario: Threshold crossing injects hint once

- **WHEN** estimated usage crosses the warn threshold
- **THEN** a hint message SHALL be appended to the projected context
- **AND** no further hint SHALL be injected on subsequent invocations until usage falls back below the threshold and crosses again

#### Scenario: Below threshold — no hint

- **WHEN** estimated usage is below the warn threshold
- **THEN** no budget hint SHALL be injected

### Requirement: Round window selection with importance ranking inside the droppable region

When selection is needed, the window processor SHALL drop units oldest-first according to the retention window, preserving system messages and the newest user unit. When importance ranking mode is enabled, importance scores (recency, role bonuses, tool-result penalties) SHALL determine drop priority among units inside the droppable region; ranking SHALL NOT create holes in a protected stable prefix when a KV-cache-aware policy is active.

#### Scenario: Oldest-first drop preserves pinned messages

- **WHEN** the context exceeds the budget and the window processor fires
- **THEN** the oldest units outside the retention window SHALL be dropped
- **AND** system messages and the newest user unit SHALL remain

#### Scenario: Importance ranking orders drops within the safe region

- **WHEN** importance ranking mode is enabled and a protected prefix exists
- **THEN** drop priority among droppable units SHALL follow importance scores (lowest importance dropped first)
- **AND** no message before the protected prefix boundary SHALL be dropped or reordered

### Requirement: Offload and explicit recall

When selection drops a block whose token count meets the offload threshold and an offloader is available, the offload processor SHALL write the dropped block to the agent environment as a JSON file and replace it in the projection with a compact stub (at most 500 characters) containing the file path, a topic summary, and a retrieval instruction. The chain SHALL expose a `recall` tool to the agent: invoking it with an offload file path SHALL return the full offloaded message block as tool result content available in-context. Recall SHALL NOT mutate the channel message history.

#### Scenario: Offloaded block replaced by stub

- **WHEN** selection drops a block of at least the offload threshold tokens and the offloader is enabled
- **THEN** the block SHALL be written to the environment as JSON preserving full message structure
- **AND** the projection SHALL contain a stub of at most 500 characters with the path and retrieval instruction

#### Scenario: Recall returns offloaded content

- **WHEN** the agent invokes the recall tool with a valid offload file path
- **THEN** the tool SHALL return the full offloaded message block as content
- **AND** the channel message history SHALL remain unchanged

#### Scenario: Recall with unknown path

- **WHEN** the agent invokes the recall tool with a path that does not exist
- **THEN** the tool SHALL return an error result identifying the missing path

### Requirement: Compression backend selection

The compression processor SHALL support pluggable backends. The default projection backend SHALL compress per-invocation in a transient, non-destructive manner (dropping or summarizing inside the projection only).

When the surface-replacement backend is selected, compression SHALL instead record a durable compaction as a bracket event sequence on the execution log, using the event types normatively defined by the compaction event schema ADR (`COMPACTION_STARTED` → `COMPACTION_SUMMARY` → `CONTEXT_SURFACE_REPLACED` → `COMPACTION_COMPLETED`). Original messages SHALL never be deleted from the log. The trigger SHALL be evaluated on the capacity axis — the effective surface token estimate against `trigger_ratio × context_window` (defaults 0.8 and the resolved model window) — at the pre-step waterline of every LLM invocation, and SHALL NOT be short-circuited by the budget-ladder satisfaction check: a projection already squeezed under the token budget SHALL NOT suppress a capacity-axis compaction. `threshold_snapshot` token values and `tokens_before`/`tokens_after` in the bracket events SHALL be measured on the effective surface (post-shadowing, pre-ladder).

Summary generation SHALL go through a compaction summarizer extension point (runtime-internal ABC; production implementation wired by the composition layer outside the runtime domain). A summary that does not reduce the effective surface below its pre-compaction size SHALL be rejected, and any summarizer failure or rejected summary SHALL leave the surface and the ladder input unchanged — a failed compaction behaves as if it never happened, and the invocation continues through the remaining degradation levels. The most recent `retain_ratio` of the window (default 0.16) SHALL never be shadowed. Re-compaction SHALL be rolling: its summary input includes the prior summary node, its shadowed range supersedes earlier overlapping ranges, and its summary event records the prior compaction id.

An unmatched `COMPACTION_STARTED` without a matching `COMPACTION_COMPLETED` SHALL be treated as an in-progress lock: a new compaction SHALL NOT start for the session while the bracket is open, unless a newer session turn boundary renders the lock stale. Surface-replacement semantics are normatively defined by the compaction event schema ADR.

#### Scenario: Default projection backend is transient

- **WHEN** compression fires with the default backend
- **THEN** the channel state and event log SHALL be unchanged and the compressed list SHALL be used for the current invocation only

#### Scenario: Surface-replacement backend records bracket events

- **WHEN** the effective surface estimate crosses `trigger_ratio × context_window` at the pre-step waterline
- **THEN** a bracket event sequence (started, summary with shadowed sequence references, surface replaced, completed) SHALL be appended to the execution log with token accounting measured on the effective surface
- **AND** the projection of subsequent invocations SHALL substitute the summary node for the shadowed range while the original messages remain retrievable from the log

#### Scenario: Capacity trigger is independent of the budget ladder

- **WHEN** the projection fits the token budget after window selection while the effective surface exceeds the capacity trigger line
- **THEN** a durable compaction SHALL still be evaluated and, when eligible, executed

#### Scenario: Non-shrinking summary is rejected

- **WHEN** the summarizer returns a summary that leaves the effective surface at or above its pre-compaction size
- **THEN** the compaction SHALL be abandoned without a surface-replace event, the surface SHALL remain unchanged, and the invocation SHALL proceed through the remaining degradation levels

#### Scenario: Unmatched start is an in-progress lock

- **WHEN** a `COMPACTION_STARTED` event exists without a matching completed event and no newer session turn boundary follows
- **THEN** the session SHALL be treated as having a compaction in progress
- **AND** a new compaction SHALL NOT start until the lock resolves

#### Scenario: Rolling re-compaction supersedes prior ranges

- **WHEN** a later compaction shadows a range overlapping an earlier compaction's range
- **THEN** the later range SHALL supersede the overlapping portion, the prior summary event SHALL remain in the log, and the later summary event SHALL reference the prior compaction id

### Requirement: Shadowing ledger view at chain entry

At the entry of every context chain invocation, the chain SHALL construct the effective surface: the `messages` channel content with every recorded surface replacement applied — the compaction summary node substituted for each shadowed range. This construction SHALL run on the common chain-entry path and SHALL apply the recorded ledger regardless of the node's chain policy; the node's backend selection governs only whether this chain may trigger a new compaction, never whether existing ledger entries are applied. The effective surface (not the raw channel, not the post-ladder projection) SHALL be the input to unitization and the measurement object of the capacity-axis trigger.

Ledger ranges SHALL be recorded as message ordinals of the `messages` channel together with content hashes of the range endpoints (anchors). Before applying a ledger entry, the chain SHALL validate both anchors against the current channel content; on mismatch the entry SHALL be skipped with a warning and the original messages SHALL pass through unshadowed. Anchor mismatch SHALL therefore degrade to a temporarily larger effective surface — never to wrongly hidden content — and the capacity trigger SHALL naturally re-compact on the current ordinals, recording a corrected entry.

#### Scenario: Ledger applies to chains without the surface-replacement backend

- **WHEN** a session has recorded compactions and a node's chain does not configure the surface-replacement backend
- **THEN** that node's projection SHALL still be built from the effective surface with recorded replacements applied

#### Scenario: Anchor mismatch fails open and self-heals

- **WHEN** a ledger entry's endpoint anchors no longer match the current channel content
- **THEN** the entry SHALL be skipped, original messages SHALL be visible, and no wrongly substituted content SHALL appear
- **AND** when the effective surface again crosses the capacity trigger, a new compaction SHALL record a corrected range

#### Scenario: Effective surface is the trigger measurement

- **WHEN** the chain entry evaluates the capacity trigger
- **THEN** the measured token estimate SHALL equal the token estimate of the effective surface after ledger application, produced by the same construction path

### Requirement: Compaction exclusivity and window resolution

Chain configuration SHALL be validated so that the surface-replacement backend and message-channel eviction are mutually exclusive: when any node's resolved chain selects the surface-replacement backend and the runtime's eviction policy for the messages channel is not the no-op default, configuration SHALL be rejected at load time with an error identifying both settings.

The context window used by the capacity trigger SHALL resolve in priority order: the model provider's recorded maximum context (injected into the execution context) → the platform's model-window capability table → hard failure at assembly time when the backend is selected and no window can be resolved. The compression processor configuration SHALL accept `trigger_ratio` (default 0.8) and `retain_ratio` (default 0.16) parameters, validated at load time alongside the backend parameter.

#### Scenario: Eviction plus surface-replacement is rejected

- **WHEN** assembly resolves a chain containing the surface-replacement backend while a non-no-op eviction policy applies to the messages channel
- **THEN** configuration SHALL fail at load time with an error naming both the backend selection and the eviction policy

#### Scenario: Window resolves from provider metadata

- **WHEN** the node's model has a recorded maximum context
- **THEN** the capacity trigger line SHALL be computed from that value

#### Scenario: Window falls back to the capability table

- **WHEN** the model has no recorded maximum context but matches the platform's model-window capability table
- **THEN** the capacity trigger line SHALL be computed from the table's window for that model family

#### Scenario: No resolvable window fails fast

- **WHEN** the surface-replacement backend is selected and no window can be resolved from provider metadata or the capability table
- **THEN** assembly SHALL fail with a configuration error before any execution

#### Scenario: Ratio parameters are validated

- **WHEN** the compression processor is configured with `trigger_ratio` or `retain_ratio` outside the valid range or of invalid type
- **THEN** configuration SHALL fail at load time with an error naming the invalid parameter

### Requirement: KV-cache awareness

The KV-cache-aware processor SHALL constrain drop and annotation decisions to maximize provider prompt-cache reuse: messages before the resolved cut point SHALL NOT be dropped or reordered across invocations of the same session unless the underlying history changed. The processor SHALL emit `cache_hint` annotations marking stable-prefix boundaries; provider shaping SHALL render these annotations into provider syntax (e.g. Anthropic `cache_control`). When the provider reports cache usage, the chain SHALL emit cache hit-rate metrics (cached tokens vs total input tokens) with the invocation telemetry.

#### Scenario: Protected prefix untouched

- **WHEN** consecutive invocations of a session have identical history prefixes and the KV-cache-aware policy is active
- **THEN** the projected context prefix up to the resolved cut point SHALL be byte-identical across those invocations

#### Scenario: Cache hint rendered by provider shaping

- **WHEN** the chain emits a `cache_hint` annotation at a cut point
- **THEN** provider shaping SHALL translate it into the provider's cache breakpoint syntax for cache-capable providers
- **AND** for providers without cache support the annotation SHALL be dropped without error

#### Scenario: Cache hit-rate metric emitted

- **WHEN** the provider response reports cached token usage
- **THEN** the invocation telemetry SHALL include the cache hit rate (cached tokens / total input tokens)

### Requirement: Controlled termination as the final degradation level

When the projected context still exceeds the budget after all processors have run, the chain SHALL degrade the turn in a controlled manner instead of silently truncating content: it SHALL strip pending `tool_calls` from the trailing assistant message(s) so the agent loop finalizes without new tool invocations, preserve system messages and the newest user message, complete the invocation, and surface `stop_reason="token_capped"` in the worker result and budget snapshot. Arbitrary content truncation SHALL NOT be used as the final degradation level.

#### Scenario: Controlled termination fires

- **WHEN** the projected context exceeds the budget after warn, truncation, selection, offload, and compression have all run
- **THEN** pending tool calls SHALL be stripped from the trailing assistant message
- **AND** the invocation SHALL complete with `stop_reason="token_capped"`
- **AND** the budget snapshot SHALL record the termination level

#### Scenario: System and newest user messages survive termination

- **WHEN** controlled termination fires
- **THEN** system messages and the newest user message SHALL remain in the projected context

### Requirement: Executor-owned failure policy

The chain executor SHALL own a failure policy for degradation processors: repeated processor failures (e.g. summarizer errors) SHALL trigger a per-session cooldown with escalating delays; a circuit breaker SHALL open after a configurable number of consecutive failures and half-open after a configurable number of successful fallback runs; re-degradation SHALL be suppressed (anti-thrash) unless new content has arrived since the last degradation or the degradation is forced.

#### Scenario: Repeated summarizer failure triggers cooldown

- **WHEN** the compression processor fails repeatedly within a session
- **THEN** further compression attempts SHALL be deferred for the cooldown duration, with the delay escalating on each subsequent failure

#### Scenario: Breaker opens and half-opens

- **WHEN** consecutive processor failures reach the breaker threshold
- **THEN** the processor SHALL be skipped in favor of the fallback degradation path
- **AND** after the configured number of successful fallback runs the breaker SHALL half-open and retry the processor

#### Scenario: Anti-thrash suppresses redundant degradation

- **WHEN** a previous degradation saved less than the configured minimum savings and fewer than the configured number of new messages have arrived
- **THEN** the chain SHALL skip re-degradation unless it is forced

### Requirement: Policy resolution across node, agent, model, and global scopes

Chain configuration (processor list, order, parameters, thresholds) SHALL resolve in priority order: node configuration > agent definition > model-capability defaults > platform defaults. Model-capability defaults SHALL key on prompt-caching support: cache-capable models SHALL default to a KV-cache-aware-first policy, non-cache-capable models to the recency-window policy. Configuration SHALL be validated at load time: unknown fields, unknown processor types, and mutually exclusive parameters SHALL be rejected before runtime. A canonical hash SHALL be derived from the fully resolved policy; identical resolved policies SHALL produce identical hashes, and the hash SHALL be exposed with agent version metadata.

#### Scenario: Node configuration overrides agent default

- **WHEN** a node configures a processor list and the agent definition configures another
- **THEN** the node list SHALL take precedence for that node's invocations

#### Scenario: Cache-capable model defaults to KV-aware-first policy

- **WHEN** no node or agent chain configuration exists and the resolved model supports prompt caching
- **THEN** the resolved policy SHALL include the KV-cache-aware processor before the plain recency window

#### Scenario: Invalid configuration rejected at load

- **WHEN** a chain configuration references an unknown processor type or conflicting parameters
- **THEN** loading SHALL fail immediately with an error identifying the invalid field
- **AND** the runtime SHALL NOT start the affected workflow with the invalid policy

#### Scenario: Canonical hash is stable for identical policies

- **WHEN** two agents resolve to the same effective chain policy
- **THEN** their policy canonical hashes SHALL be equal

### Requirement: Runtime hint injection

The chain SHALL support runtime hint injection: elapsed time since the last time hint, pending task state, and context usage may be appended to the projected context as persistent hint messages. Hints SHALL NOT modify the system prompt, and each hint type SHALL be injected only when its condition holds (time interval elapsed; task state changed or tasks pending without related tool activity; usage near the warn/compaction threshold). Hint injection SHALL be recorded as hint messages in the projected context, visible to the model on subsequent turns.

#### Scenario: Time hint after interval

- **WHEN** more than the configured interval has elapsed since the last time hint
- **THEN** a hint message stating the elapsed time SHALL be appended to the projected context

#### Scenario: Usage hint near compaction threshold

- **WHEN** estimated usage is within the configured buffer of the compaction/warn threshold
- **THEN** a hint message with current usage and the threshold SHALL be appended
- **AND** the usage buffer threshold SHALL be strictly below the warn/compaction threshold

#### Scenario: Hints never modify the system prompt

- **WHEN** any runtime hint is injected
- **THEN** the system prompt SHALL remain byte-identical to its pre-injection form
