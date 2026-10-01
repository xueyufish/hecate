# Hosted backend mapping semantics (0.x draft)

How platform task/run semantics map onto a hosted vendor's session/turn model.
The vendor owns its session state; the platform keeps only the necessary
references, received statuses, and verifiable evidence. This document fixes
the MAPPING semantics; real hosted-service verification belongs to plan step8.

## Reference mapping

| Platform side | Vendor side | Rule |
|---|---|---|
| `task` ref (platform-issued) | one or more `session` refs (vendor-issued) | One platform task may span several vendor sessions (retries create new runs, and a session may outlive a run attempt). The task reference never becomes a session reference. |
| `run` ref in ExecutionRequest (platform-issued) | backend-issued `run` ref in SubmitReceipt | Keep both references in the adapter mapping. Events and status queried from the backend use its reference; the platform preserves its own Run ID. Matching `kind=run` does not make references from different issuing domains interchangeable. |
| backend-issued `run` ref | primary vendor `session` ref + turn position, when the vendor has sessions | The adapter owns this mapping and any additional child-session references in its namespace. A backend need not expose a session at all. `run`, `session` and `turn` remain distinct kinds. |
| `deployment` ref | vendor harness + environment registration | Harness owner and environment owner are declared on separate capability axes; a self-hosted sandbox does not make the session self-hosted. |
| event cursor | vendor session/turn position | Cursors are opaque and per-run; the platform never reconstructs a global order from vendor sequences. |

## State projection

Vendor-reported states are RECEIVED statuses, not platform-owned facts: the
platform stores them with source and sequence. A vendor-confirmed cancel or
stop is the vendor's claim; the platform's own cancel receipt states
(`requested`/`acknowledged`/`applied`/`rejected`/`unknown`) remain the record
of what the platform actually knows. Lost submit responses: reconcile by
`query_by_idempotency_key` where the vendor supports idempotent submission, or
by `query_by_vendor_session` where the backend declares
`reconciliation_support.query_by_vendor_session`; otherwise mark the run
`unknown` and stop new protected work on it. Blindly re-creating a session is
forbidden.

## Subagent events

Vendor-internal subagents, turns, and handoffs map to NAMESPACED
run-internal detail events (backend-specific `payload_schema_ref`, backend
namespace). They never generate platform Team membership, delegated
authority, or independent principals (plan step12 boundary); only agents
registered and admitted by the platform become enterprise members.

## Vendor configuration

Vendor-specific configuration lives in `backend_config_ns` (per-request) and
backend config references (per-deployment). The core validates nothing inside
a vendor namespace and no vendor setting can grant platform permissions.
