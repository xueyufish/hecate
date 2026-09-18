## Purpose

Provides in-band citation provenance for tool-grounded agent output: tool results entering conversation history are deterministically chunked and prefixed with session-scoped citation markers, a session-level append-only registry resolves every marker for the lifetime of the session, model responses are back-mapped to the chunks they cite, and the ratio of uncited factual sentences is recorded as a warn-level risk signal. The layer is observational — it never intercepts, blocks, or rewrites a response — and constitutes the grounding-source substrate on which a later scoring stage will build.

## Requirements

### Requirement: Chunk marking at tool result ingestion

When citation provenance is enabled for a node, each tool result message SHALL be chunked deterministically (configurable granularity, default approximately 200–300 characters) as it is written into conversation history. Each chunk of at least the configured minimum size SHALL receive a citation marker of the form `【N-M】` (session-scoped, monotonically increasing identifiers) prepended to the model-visible content of that chunk. The raw, unmarked tool result content SHALL remain retrievable so programmatic consumers are unaffected. Tool results below the minimum size SHALL pass through unmarked.

#### Scenario: Tool result is marked at write time

- **WHEN** a tool returns a text result larger than the minimum size and the node has citation provenance enabled
- **THEN** the message written to conversation history SHALL contain `【N-M】`-prefixed chunks
- **AND** the raw unmarked content SHALL remain retrievable from the message record

#### Scenario: Small tool results pass through unmarked

- **WHEN** a tool result is smaller than the configured minimum size
- **THEN** the message SHALL be written unchanged with no citation markers

#### Scenario: Markers are written once and remain stable

- **WHEN** subsequent invocations of the same session project the message again
- **THEN** the marker identifiers and their positions SHALL be byte-identical across invocations

### Requirement: Session-scoped append-only chunk registry

The layer SHALL maintain a per-session registry mapping every issued citation marker to its source chunk (tool call id, chunk text, position). Identifiers SHALL never be reused within a session. A registry entry SHALL remain resolvable for the lifetime of the session regardless of projection state: dropping, offloading, or compressing the underlying message in a projection SHALL NOT invalidate the entry. The registry SHALL NOT modify channel state or the event log of prior turns.

#### Scenario: Registry entry resolvable after projection drop

- **WHEN** a response cites a chunk whose message was dropped from the current projection by window selection
- **THEN** back-mapping SHALL still resolve the citation to the registered chunk content

#### Scenario: Identifiers are never reused

- **WHEN** a session accumulates tool results across many turns
- **THEN** each issued marker maps to exactly one chunk and no identifier is ever reissued

### Requirement: Citation back-mapping of responses

After an LLM response is produced for a node with citation provenance enabled, the layer SHALL extract all citation markers present in the response text deterministically (pattern scan, no model call) and record the citation map (claim-bearing response → cited registry entries) as message metadata. Responses referencing unknown or malformed markers SHALL have those references recorded as unresolved citations rather than silently dropped.

#### Scenario: Cited response produces citation map

- **WHEN** a response contains `【3-2】` and `【3-5】` markers
- **THEN** the response message metadata SHALL record a citation map referencing registry entries 3-2 and 3-5

#### Scenario: Unknown marker recorded as unresolved

- **WHEN** a response contains a marker that was never issued in this session
- **THEN** the citation map SHALL record the reference as unresolved
- **AND** the response itself SHALL NOT be modified

### Requirement: Citation ratio risk signal at warn level

For each response on a node with citation provenance enabled, the layer SHALL compute the ratio of factual sentences lacking any citation marker and record it as a warn-level risk signal in the invocation's audit event. The signal SHALL be observational only: the response SHALL NOT be intercepted, blocked, rewritten, or delayed, and no judge model SHALL be invoked in this stage.

#### Scenario: Uncited ratio recorded

- **WHEN** a response contains four factual sentences of which one carries no citation marker
- **THEN** the audit event SHALL record the uncited ratio (0.25) with the warn level

#### Scenario: Signal never intercepts the response

- **WHEN** the uncited ratio equals or exceeds any threshold value
- **THEN** the response SHALL still be delivered unmodified
- **AND** no judge model call SHALL be made

### Requirement: Provenance audit events

The layer SHALL append additive audit events to the EventStore when chunks are registered, when a citation map is produced, and when the risk signal is computed. The event vocabulary SHALL be additive: consumers of existing events SHALL be unaffected, and event emission failure SHALL be logged without affecting the invocation.

#### Scenario: Audit event emitted without affecting invocation

- **WHEN** citation provenance produces a citation map for a response
- **THEN** an audit event referencing the session, node, and citation map SHALL be appended to the EventStore
- **AND** a failure to append the event SHALL NOT fail the invocation

### Requirement: Interaction with projection degradation

Citation markers SHALL be compatible with the context processor chain's degradation stages: tool-result truncation cuts only chunk tails so markers at chunk heads survive, and the registry SHALL record truncated chunks as partially present. Offloaded chunks SHALL remain resolvable in the registry, and the recall tool restoring offloaded content SHALL NOT invalidate or renumber existing markers.

#### Scenario: Truncated chunk recorded as partial

- **WHEN** tool-result truncation shortens a marked chunk
- **THEN** markers in the retained head SHALL survive unchanged
- **AND** the registry SHALL record the chunk as partially present

#### Scenario: Recall does not renumber markers

- **WHEN** the recall tool reloads an offloaded block containing marked chunks
- **THEN** the restored chunks SHALL carry their original markers
- **AND** no marker in the session SHALL be renumbered

### Requirement: Citation badge display in studio

The citation map recorded on assistant messages (cited and unresolved chunk markers) SHALL be surfaced in studio session conversation and replay views as a badge display on the message: the cited marker count, the unresolved marker count, and the individual marker identifiers on demand. Unresolved markers SHALL be visually distinguished from resolved ones. Messages without citation metadata SHALL render without badges. The display is read-only: no scoring state, highlighting, or disposition information is included in this stage.

#### Scenario: Message with citations renders badge

- **WHEN** a studio session or replay view renders an assistant message that carries citation metadata
- **THEN** the message SHALL display a citation badge with the cited count, and the individual marker identifiers SHALL be viewable on demand

#### Scenario: Unresolved markers distinguished

- **WHEN** a citation map contains unresolved markers (never issued in the session)
- **THEN** those markers SHALL be visually distinguished from resolved markers in the badge detail

#### Scenario: Message without citations renders nothing

- **WHEN** an assistant message has no citation metadata (provenance disabled for its node)
- **THEN** no citation badge SHALL be rendered for that message
