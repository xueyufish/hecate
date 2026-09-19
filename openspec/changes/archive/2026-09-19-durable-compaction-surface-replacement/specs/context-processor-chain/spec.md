## MODIFIED Requirements

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

## ADDED Requirements

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
