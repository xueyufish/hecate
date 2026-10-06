# hecate-runner

Standalone execution host for the [`hecate-runtime`](../hecate-runtime) kernel.
It cold-starts from a profile directory with no management platform, no platform
management tables, and no repository source path. The same HTTP paths now expose
both the legacy preview shape and the step3 execution-backend binding; formal
clients submit an `ExecutionRequest` with `Idempotency-Key` and use the returned
structured backend `run_ref`.

The host and the platform builtin backend consume the same
`hecate_runtime.execution_service` application behavior. Each side keeps its own
identity, evidence, storage, and persistence adapters.

## Status: technical preview

Not production. The base profile explicitly does **not** provide durable tasks,
background retries, long-task recovery, write or approval tools, event
streaming, or any remote-revocation guarantee. The optional durable profile adds
persistent local tasks/actions, replay-gated recovery, local write tools, and
restart-queryable execution events. It still does not provide production
certification, managed authorization guarantees, persistent input/approval
waiting, or an event stream. Execution is serial (one run at a time). Do not
expose the service beyond localhost.

The default profile admits read-only tools. An opt-in `durable` profile adds
persistent task/action records, write-tool recovery and bounded serial restart
admission. With both `durable` and `control_plane` configured, the host joins
the managed closed loop: registered deliveries land in the persistent receive
queue (idempotent accept; accept means accepted, never running), a serial
scheduler drives accepted tasks through the same engine slot, task facts and
terminal results upload to the platform projection with per-run cursors
(reconnect backfill is deduplicated upstream), and shutdown drains honestly.
Action-time lease enforcement, persistent waiting, and checkpoint recovery are
later-step scope. Capability statements describe available primitives, not
production certification.

## Install

```bash
uv build --package hecate-runner
pip install dist/hecate_runner-*.whl   # pulls hecate-runtime and hecate-durable — never the full hecate app
```

The wheel carries the authoritative execution-contract JSON Schema snapshot in
`hecate_runner/_contract_schemas`. The source of truth remains
`src/hecate/contracts/schemas`; run
`python packages/hecate-runner/scripts/sync_contract_schemas.py` after changing
it. `packages/hecate-runner/tests/test_schema_publication.py` fails if the
published snapshot drifts.

## Profile layout

```
<profile>/
  agent-manifest.json     # kernel ArtifactManifest; digests verified against files/
  runner.json             # host config (see below); secrets by reference only
  identity.json           # trust material: credential refs -> {principal, role: read_only, domains}
  files/                  # manifest-listed content, at their manifest paths
  evidence/               # created at startup; day-rolled JSONL evidence
```

`runner.json` example:

```json
{
  "host": "127.0.0.1",
  "port": 8600,
  "evidence_dir": "evidence",
  "business_api_base": "http://127.0.0.1:8601",
  "model": {"backend": "stub"},
  "tool_allowlist": ["query_inventory"],
  "shutdown_token_ref": "env:RUNNER_SHUTDOWN_TOKEN",
  "evidence": {"retention_days": 30, "capacity_limit": 104857600}
}
```

## Durable profile

Add an explicit host-owned database and deployment/data domain:

```json
{"durable": {"database_url": "postgresql+psycopg://USER:PASSWORD@HOST/DATABASE", "workspace": "customer-a"}}
```

Install `hecate-durable[postgres]` for the PostgreSQL driver. SQLite is a
local development option. Back up and upgrade the host schema while drained;
`create_schema()` creates missing tables, and is not a general migration tool.
Recovery trusts the persisted principal/domain snapshot only after checking
current `identity.json`; it never substitutes client-provided identity fields.

## Managed profile (control_plane)

With the durable profile present, an optional `control_plane` block joins the
managed closed loop:

```json
{
  "control_plane": {
    "base_url": "https://platform.example",
    "workspace_id": "<workspace uuid>",
    "trust_root": "host-root-1",
    "host_id": "host-1",
    "issuer_domain": "platform-issuer",
    "secret_ref": "env:MANAGED_TRUST_SECRET",
    "lease_ttl_seconds": 120,
    "data_domains": ["domain_a"]
  }
}
```

- `control_plane` without the durable profile refuses startup — the managed
  receive queue is the persistent task ledger.
- Accepted deliveries map to local tasks `managed-<delivery-id>` under the
  `managed-host` issuer and carry an accept-time identity stamp
  (`principal: managed:<trust_root>`, `domains: data_domains`); recovery
  re-checks the stamp against the current configuration and stops drifted
  tasks into reconciliation instead of re-homing them.
- `data_domains` scopes which data domains managed tool dispatches may
  address; the default (empty) is deny-by-default at the existing domain
  check.
- Protected (non-readonly) managed dispatches pass the shared lease gate at
  the action boundary — signature, expiry, deployment binding, an
  unconsumed nonce, and the requested domain inside the lease's `scope`
  (operator-controlled `managed_scope`, issued by the platform on every
  pull). One lease authorizes one protected action; refusals are explicit
  evidence with zero business side effects and never fall back to local
  self-authorization. Readonly dispatches and standalone runs bypass the
  gate entirely.
- Disconnects degrade to local retry: pulls/uploads fail soft, executions
  continue from the local queue, and events backfill per run on reconnect
  (upstream deduplicates per event id). An expired lease stops new protected
  actions and never falls back to local self-authorization; unknown-outcome
  actions reconcile through the action ledger and are never redone after
  re-authorization.

## Evidence retention

Audit evidence retention is explicit. The optional top-level `evidence` block
carries:

- `retention_days` — records older than this age are cleaned up (deletion is
  traced in the evidence itself); absent means no expiry, ever.
- `capacity_limit` — when usage exceeds the limit the store first runs
  retention cleanup and re-checks; still over, new protected (non-`readonly`)
  actions are refused with the `capacity` failure category until space frees.
  Units are declared by the backend: the default JSONL backend counts bytes,
  the SQL backend (below) counts rows. Absent means no limit.
- `dir` — optional evidence directory override (default: `evidence_dir`).
- `backend` — `jsonl` (default) or `durable`.

An unconfigured block is itself explicit: the capability summary and
`GET /healthz` report `evidence_policy_configured: false` with no limits and
no expiry — nothing is truncated or dropped silently. Gate failures carry an
explicit category (`unwritable` vs `capacity`), are recorded as auditable
`evidence_gate` denials, and surface in the health report
(`evidence_last_gate_failure`). Host bookkeeping (gate probes, cleanup traces)
never counts toward the capacity meter, so a cleanup can always reopen the
gate; bookkeeping records still age out through retention.

Durable-profile hosts may delegate audit retention to their own local SQL
database — `{"backend": "durable"}` shares the persistent task store's engine
and adds an `evidence_records` table to the host's database. This is a
host-local storage delegation: it never writes platform tables and adds no
upload path. Both backends run the same parametrized assertion suite.

Secrets are referenced, never inlined: `env:NAME` or `file:relative/path`.
`model.backend` is `stub` (deterministic, CI) or `endpoint` (calls
`model.endpoint` and records that source in the run's evidence — no vendor
certification is implied).

The endpoint adapter sends `{"prompt": "...", "tools": ["query_inventory"]}`
and expects `{"content": "..."}`; optional `model.auth_env` references a bearer
token. Endpoint errors fail the run. This profile uses a fixed tool plan and
does not implement model-driven tool selection. The default profile maps `query_inventory`; the durable profile additionally
allows manifest-declared `write_inventory`. Other mappings fail startup. Its arguments
require non-empty string `domain` and `sku`; declared tool schemas must be listed
and digest-verified in the manifest.

## Run

```bash
hecate-runner --profile ./my-profile [--business-api http://127.0.0.1:8601]
```

## HTTP surface

Formal execution-backend binding: `GET /capabilities`, `POST /runs`,
`GET /runs/{issuer_domain}/{run_id}`, `GET /runs/{...}/events?cursor=`,
`POST /runs/{...}/cancel`, and `GET /runs/{...}/artifacts`. Formal submissions
require the body `ExecutionRequest` and `Idempotency-Key` header to use the same
key; receipts, status, normalized events, artifacts, cancel receipts, and
`version_conflict` errors follow the schemas in this wheel. The legacy preview
body and string `runs/{id}` references remain compatible. Host extensions:
`GET /healthz`,
`GET /v1/evidence?outcome=&principal=`, `POST /admin/shutdown`
(token in JSON body). Errors are `urn:hecate:problem:*` problem+json.

Authenticate with `Authorization: Bearer <credential>` where the credential
hashes to an entry in `identity.json`. Principal, role, and data domains are
resolved server-side; role/domain fields in the request body are ignored by
design.

Health and capability probes are public. Run state/events/artifacts and cancel
require the original principal with the original domains still in scope.
Evidence queries return only the caller's principal; deployment operators read
anonymous-denial records from local JSONL files. A run is admitted only after
its local evidence is flushed; an unwritable store refuses execution. Cancellation
stops later tool calls cooperatively and cannot revoke an external call already
in progress. Shutdown stops admission and closes in-process tasks; unresolved
work is recorded as `unknown`. Durable restart validates the retained identity
and action ledger; unresolved protected results require reconciliation.

## Persistent waiting and wake (durable profile)

A durable host can park a task into a persistent wait instead of dispatching:

- a manifest-declared `approval_required` tool records its action intent and
  claim, then parks the task into `waiting_approval` — the business call waits
  for a recorded decision (the judgment itself is step7 scope);
- a business API answer of `{"status": "input_required", "contract": ...}`
  parks the task into `waiting_input` with no further dispatch.

The wait record binds the original Task/Run, the tool and argument digest, a
contract reference, a one-time `wait_token`, and a deadline, committed in the
same transaction as the state transition. Waiting tasks hold no execution slot
and are never re-driven automatically — they survive hard restarts as waits.

Wake it with an authorized command (run-owner bearer identity):

```
POST /runs/{run}/resume         {"command_id": "...", "wait_token": "..."}
POST /runs/{run}/provide-input  {"command_id": "...", "wait_token": "...", "input": {...}}
```

A valid wake applies atomically: token consumed, provided input merged into
the task input (filling gaps in each tool's arguments), the task requeued on a
NEW attempt run, and the command receipt flipped to `applied` — then the host
re-drives it through the serial slot once. `GET /runs/{run}` shows the wait
record (including the token) to the run's owner while it waits. Expired
commands, expired waits, wrong tokens, and command replays return explicit
`rejected` receipts or the idempotent original — nothing dispatches. The
preview (non-durable) profile still refuses `approval_required` tools: no
persistence, no reliable waiting.

## Scenario coverage

SC01 (clean-install cold start) and SC02 (structured inventory read and
unauthorized calls) run via `tests/scenarios/test_sc01_cold_start.py` and
`tests/scenarios/test_sc02_inventory_read.py`; the shared harness in
`tests/scenarios/tools/runner_harness.py` builds both wheels, installs them
into a clean venv, and drives the host over HTTP.
