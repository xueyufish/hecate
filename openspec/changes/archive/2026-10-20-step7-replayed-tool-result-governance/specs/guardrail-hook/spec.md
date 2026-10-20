# Spec Delta

## ADDED Requirements

### Requirement: Successful replay reuses PostTool output governance

The shared tool call executor SHALL re-apply the same PostTool output governance it applies on first execution — the `TOOL_RESULT` middleware chain taking precedence over the legacy `PostToolHook` — to ledger-backed content at every successful replay exit (recovery pre-check, authoritative in-lock re-check, and ledger claim-race backfill) before that content is returned as a tool message. A `BLOCK` SHALL yield the same governed placeholder shape as first execution; a `SANITIZE` SHALL apply `modified_data["result"]`; an `ALLOW` SHALL pass the content through unchanged. The ledger-recorded content is fed to governance as recorded; governance SHALL NOT assume it is the original structured result object. A governance failure on the replay path SHALL NOT return the original content and SHALL fail closed under the existing unknown/reconciliation semantics. Replay governance is local output processing only: it SHALL NOT re-execute the business tool, re-consume an approval, or re-claim a settled action, and SHALL NOT rewrite a ledger-recorded business success into a failure. First-execution governance and the durable outcome-mirroring order (ledger write before PostTool) SHALL remain unchanged, as SHALL the channel-history backfill path whose content is already the first-run governed result.

#### Scenario: Replay applies chain precedence

- **WHEN** a successful replay resolves content from the ledger and a `TOOL_RESULT` middleware chain is configured
- **THEN** the executor applies the chain — not the legacy hook — to that content before returning it, mirroring first-execution precedence

#### Scenario: BLOCK on replay hides first-run content

- **WHEN** first execution stored the raw result in the ledger and PostTool governance blocks the same content on replay
- **THEN** the replayed tool message carries the governed placeholder, the business tool is not re-executed, and the ledger outcome remains `succeeded`

#### Scenario: SANITIZE on replay masks content

- **WHEN** the governance pipeline returns `SANITIZE` with `modified_data["result"]` for replayed content
- **THEN** the returned tool message carries the masked content

#### Scenario: Governance error fails closed

- **WHEN** output governance raises an exception on a replay path
- **THEN** the executor stops under the existing reconciliation semantics and the original content never reaches the model or channel

#### Scenario: Channel-history backfill stays governed-once

- **WHEN** the channel history already holds the first-run tool message for the call
- **THEN** the executor returns it as-is without applying governance a second time

#### Scenario: No hook or chain configured

- **WHEN** no `TOOL_RESULT` middleware chain and no matching legacy `PostToolHook` is configured
- **THEN** replayed ledger content passes through unchanged, identical to first-execution behavior

#### Scenario: Empty recorded content still passes through governance

- **WHEN** the ledger records a successful result whose content is an empty string
- **THEN** replay SHALL apply the configured output governance; an empty string SHALL NOT be treated as missing content

#### Scenario: Claim-race backfill preserves an already governed channel result

- **WHEN** a competing ledger claim resolves as succeeded and recovery selects an existing channel tool message
- **THEN** the executor SHALL return that governed message without treating it as ledger raw content or applying PostTool twice

#### Scenario: Replay governance receives trusted execution correlation

- **WHEN** a ledger result is passed to output governance during recovery pre-check or claim-race backfill
- **THEN** the context SHALL carry the actual tool_call_id and server-derived stable invocation_id, overriding caller-supplied values without mutating the caller context
