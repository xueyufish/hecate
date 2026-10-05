# hecate-runner

Standalone execution host for the [`hecate-runtime`](../hecate-runtime) kernel —
the step5c technical preview. It cold-starts from a profile directory with no
management platform, no platform management tables, and no repository source
path, and serves a host-specific preview HTTP API in a strictly
**read-only** posture.

This API is not yet the complete step3 execution-backend binding: the preview
uses its own run request, string references and event shape. The contract adapter
and shared execution application service remain step5c work; do not register it
as a certified interchangeable backend based on endpoint names alone.

## Status: technical preview

Not production. The preview explicitly does **not** provide: durable tasks,
background retries, long-task recovery, write or approval tools, event
streaming, or any remote-revocation guarantee. These are declared
`unsupported` on `/capabilities` and land in step6/7/10/11. Execution is
serial (one run at a time). Do not expose the service beyond localhost.

## Install

```bash
uv build --package hecate-runner
pip install dist/hecate_runner-*.whl   # pulls hecate-runtime only — never the full hecate app
```

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
does not implement model-driven tool selection. Only `query_inventory` is mapped
by the preview business adapter; other mappings fail startup. Its arguments
require non-empty string `domain` and `sku`; declared tool schemas must be listed
and digest-verified in the manifest.

## Run

```bash
hecate-runner --profile ./my-profile [--business-api http://127.0.0.1:8601]
```

## HTTP surface

Preview host API: `GET /capabilities`, `POST /runs`, `GET /runs/{id}`,
`GET /runs/{id}/events?cursor=`, `POST /runs/{id}/cancel`,
`GET /runs/{id}/artifacts`. Host extensions: `GET /healthz`,
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
work is recorded as `unknown`, without recovery guarantees.

## Scenario coverage

SC01 (clean-install cold start) and SC02 (structured inventory read and
unauthorized calls) run via `tests/scenarios/test_sc01_cold_start.py` and
`tests/scenarios/test_sc02_inventory_read.py`; the shared harness in
`tests/scenarios/tools/runner_harness.py` builds both wheels, installs them
into a clean venv, and drives the host over HTTP.
