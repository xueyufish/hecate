# hecate.contracts — language-neutral execution contract

Authoritative JSON Schema files live in `schemas/` (each carries an `$id` and a
0.x draft version; `errors` and `capabilities` are at 0.2). The `execution/`
subpackage is a stdlib-only Python mapping (dataclasses, no pydantic) — one
binding among many, never the source of truth. Standard samples live in
`tests/test_execution/samples/`.

Three-way consistency (samples pass schema validation; the mapping parses every
sample; round-trips preserve fields) is enforced by `tests/test_execution/`.

## Durable execution contracts

`durable-task-state.schema.json`, `durable-command-record.schema.json`, and
`durable-action-ledger.schema.json` fix the shared vocabulary for step6's
parallel deliveries — the persistence core (`durable-execution-core`) and the
platform task API (`platform-task-control-api`) both consume these and must not
define parallel enums or interfaces:

- Task lifecycle states are a closed enum, a different dimension from the
  backend-observed `run-status` states; terminal states absorb and
  `reconciliation_required` converges only through an explicit reconciliation
  action. Field ownership stays a consumer concern.
- Control commands carry independent receipt records (`requested /
  acknowledged / applied / rejected / expired`); a transport success is never
  `applied`, and `expired` is terminal. `provide_input` payloads follow the
  envelope pattern (`payload` + `payload_schema_ref`); the concrete payload
  schema is defined by consumers, not pre-built here.
- The action ledger mirrors the runtime `tool-recovery` four-state semantics
  (`never_started / claimed / outcome_unknown / store_unavailable`) with
  terminal outcomes split into `ActionOutcome`; drift tests pin the mapping,
  the Python enums, and the schema enums together.
- The governance-event profile reuses `event-envelope.schema.json` with
  optional `actor`/`source` fields — both required under the profile
  (`validate_governance_event`), so platform-observed and remotely reported
  events stay attributable and gradeable.

Minimal seams (`DurableTaskStore`, `ControlCommandRecorder`, `ActionLedger`)
live in `hecate.execution.durable` with an InMemory second implementation in
`hecate.execution.stub_durable`. Every production implementation must pass the
parameterized suite in `tests/test_execution/test_durable_contract.py` before
wiring — register the factory in `DURABLE_IMPLEMENTATIONS`
(`tests/test_execution/conftest.py`) to inherit the suite unchanged. The SQL
reference implementation (`hecate-durable`, worktree A of step6) is registered
there as `sql-sqlite`; its fault-injection suite runs against PostgreSQL via
`DURABLE_TEST_POSTGRES_URL`.

Since step6's `durable-execution-core`, the durable contract cluster
(`references` / `events` / `tools` / `durable`) and the seams actually live in
the independently installable `hecate-durable` package
(`hecate_durable.contracts` / `hecate_durable.seams` / `hecate_durable.stub`);
the paths under `hecate.contracts.execution` / `hecate.execution` forward there
so the standalone-host wheel closure never pulls the full application. The
authoritative schemas stay in `src/hecate/contracts/schemas/`.

## HTTP/JSON binding and hosted mapping

- `openapi/execution-backend.http.v0_1.yaml` is the first out-of-process
  transport binding (OpenAPI 3.1). It references the authoritative schemas by
  `$id` — field definitions are never copied into the binding. Error
  responses use RFC 9457 problem+json: `unsupported`→501,
  `authorization_denied`→403, `budget_exhausted`→429, `version_conflict`→409;
  `unreachable` and `outcome_unknown` are caller-synthesized states, never
  backend HTTP error responses (`outcome_unknown` is a run-status value).
  SSE streaming is an optional view; the cursor read endpoint is always
  sufficient. Structure is validated by `openapi-spec-validator` (dev extra);
  HTTP sample pairs live in `tests/test_execution/samples/http/`.
- `hosted-mapping.md` fixes the platform task/run ↔ vendor session/turn
  mapping semantics, subagent-event namespacing, and the
  `query_by_vendor_session` reconciliation flow.
- Identity travels as the claim set in `security-claims.schema.json` (issuer,
  audience, workload subject, tenant, expiry) — claim possession is not
  authentication; verification belongs to the receiving adapter. Identity
  never comes from the request body.

## Encoding rules

- **Identifiers** are opaque non-empty strings wrapped in logical references
  (`kind` / `issuer_domain` / `id`). Receivers resolve references from the
  request and trusted local registration — never by querying a platform ORM.
  Platform-side kinds (task, run, deployment, authorization, artifact, evidence,
  approval) and vendor-side kinds (session, turn) are distinct; one kind must
  never stand in for another.
- **Timestamps** are RFC3339 UTC with the `Z` suffix (e.g.
  `2026-09-30T10:00:00Z`).
- **Enums** are lowercase snake_case strings (`outcome_unknown`,
  `hecate_gateway`); the closed error set is: `unsupported`,
  `authorization_denied`, `budget_exhausted`, `version_conflict`, `unreachable`,
  `outcome_unknown`.
- **Nullable fields** are expressed by absence, not JSON `null`, unless the
  schema explicitly allows it. Optional fields may be omitted entirely.
- **Unknown fields** MUST be tolerated by receivers on objects whose schema sets
  `additionalProperties: true` (requests, envelopes, errors); the Python
  mapping preserves them in an `extra` map so round-trips never silently drop
  forward-compatible extensions. Payload objects referenced by
  `payload_schema_ref` carry their own schemas.
- **Version negotiation**: senders declare the contract version they speak in
  `contract_version`; a version outside the receiver's support window is
  answered with `version_conflict` (never reinterpreted). The execution
  contract and the sandbox contract are versioned independently.
- **Idempotency keys** are opaque strings scoped by the caller; replaying a key
  with identical content returns the original result, and replaying it with
  different content is a `version_conflict` whose `detail_ns.reason` is
  `idempotency_key_content_mismatch`.
  For HTTP, the scope is the authenticated issuer/tenant/workload, and the
  `Idempotency-Key` header MUST equal the body's `idempotency_key`. Body refs
  cannot establish that scope or grant access to another caller's run.
- **Trace correlation** travels as `trace_id` (+ optional `parent_span_id`);
  event envelopes add `correlation_id` / `causation_id`.

## Dependency rules

- No SQLAlchemy, FastAPI, Pregel engine modules, or vendor SDKs — enforced by
  `tests/test_execution/test_contract_purity.py` (AST scan over every import
  position).
- `hecate.contracts` is shared vocabulary: any domain may consume it.
  `hecate.execution` is an extension-point package: consumed via
  `core/composition` and tests only (registered in the layering guard).
- Inbound wire validation uses these same schema files (`jsonschema` in Python,
  Ajv in the TypeScript pilot). Contract DTOs and archive checks remain stdlib
  only. `contracts` cannot import `execution`; `execution` may consume the
  shared contracts. The import guard allows only stdlib and this direction.

## Capability scope and evidence

The five original interaction names remain required. The optional control
names `cancel`, `events_resume`, `tool_proxy`, `sandbox`, `callback`,
`internal_tools_visibility`, and `subtask_tracking` default to `unsupported`
when absent. Each non-unsupported name requires a verification entry in both
the schema and Python mapping. An `enforced` declaration additionally requires
backend/contract versions, deployment shape, expiry, an evidence reference,
and `observation_source=platform_observed|independent_test`. A backend report
alone cannot claim enforced control. These fields describe evidence; admission
must still authenticate its provenance, match the actual deployment/version,
and reject expired evidence (steps4/7/8). A string reference is not proof.

Optional interaction refusal is available over HTTP; successful interaction
payloads/receipts need an explicit adapter profile and verification. The pilot
declares all five interactions unsupported and demonstrates a real wire pause
refusal. The six-method minimum remains unchanged.

## Cursor semantics

Return `next_cursor` even at the current live tail, including empty pages.
`has_more=false` means no more events are available **now**, not that the run
has ended. Keep the cursor so newly emitted events can be read without replay.
Gap markers have a sequence after the declared missing range.

## Local artifact installation profile (draft)

The first distribution is a tar.gz with exactly the listed regular files.
Use portable relative paths; reject absolute/drive/backslash/traversal paths,
duplicate archive names, links, devices, unlisted members and unlisted entry
points. Unknown manifest declarations, including installation hooks, are
rejected by the runtime guard as well as the schema. Verification reads bytes
and never extracts files or executes an installer.

`publisher_ref`, `license_expression`, and `signature_ref` carry local trust
metadata. Missing metadata means unverified, not implicitly trusted. The step5
loader must resolve the publisher against host-configured trust roots, verify
a detached signature/attestation binding the exact manifest and file digests,
and apply the host's license/size/resource policies before loading. Caller
content cannot change those policies. Preview acceptance of unsigned artifacts
must be explicit; managed admission requires verified evidence. Digest
validation proves integrity, not publisher identity, secret absence or business
data classification. Credential-shaped key rejection is only a coarse guard.
OCI support and catalog publication remain later-step work.
