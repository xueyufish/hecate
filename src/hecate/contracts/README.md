# hecate.contracts — language-neutral execution contract

Authoritative JSON Schema files live in `schemas/` (each carries an `$id` and a
0.x draft version). The `execution/` subpackage is a stdlib-only Python mapping
(dataclasses, no pydantic) — one binding among many, never the source of truth.
Standard samples live in `tests/test_execution/samples/`.

Three-way consistency (samples pass schema validation; the mapping parses every
sample; round-trips preserve fields) is enforced by `tests/test_execution/`.

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
- **Trace correlation** travels as `trace_id` (+ optional `parent_span_id`);
  event envelopes add `correlation_id` / `causation_id`.

## Dependency rules

- No SQLAlchemy, FastAPI, Pregel engine modules, or vendor SDKs — enforced by
  `tests/test_execution/test_contract_purity.py` (AST scan over every import
  position).
- `hecate.contracts` is shared vocabulary: any domain may consume it.
  `hecate.execution` is an extension-point package: consumed via
  `core/composition` and tests only (registered in the layering guard).
- Runtime validation of inbound payloads uses `jsonschema` against these same
  schema files — a single validation authority.
