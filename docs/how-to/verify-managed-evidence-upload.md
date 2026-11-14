# Verify managed evidence upload recovery

A managed Runner keeps executing from its local queue when the platform cannot
take its evidence, then backfills the platform projection from the local event
log. This guide covers what the Runner guarantees on that path, how operators
read the upload state, and how to run the installed-Runner drill that verifies
it.

## What the Runner guarantees

The Runner uploads each run's durable event stream to `POST /managed/host/events`
in batches of at most `control_plane.upload_batch` envelopes per run and cycle.

- **Explicit confirmation only.** A run's upload position advances only when the
  platform's answer accounts for every envelope of the batch
  (`projected + skipped_duplicates` equals the batch size, both non-negative
  integers). A transport failure, a rejection, an empty or non-JSON body, or
  counts that miss part of the batch confirm nothing. The next cycle retries the
  same batch before sending later events. Confirmation requires a successful
  HTTP status and complete counts.
- **Lost answers are harmless.** The platform commits a batch in one transaction
  and deduplicates by event id under the delivery's original Task/Run
  association. When the answer to a committed batch is lost, the resend comes
  back as duplicates (`projected: 0`), and that complete confirmation advances
  the position. The same event id with different facts is still rejected.
- **Runs are independent.** Positions are kept per run stream. A confirmation
  for one run never moves another run, and a run the platform keeps refusing
  does not stop other runs from uploading.
- **Restart resends, never re-executes.** Positions live in memory. After a
  restart the Runner resends each run from its first event and the platform
  deduplicates. Uploading evidence never repeats a business action.
- **Local gaps are never crossed.** If the local event log is missing a sequence
  range inside a run (for example, rows lost from the host database), the Runner
  uploads the events before the gap, holds that run there for reconciliation and
  keeps uploading other runs. The retained events after the gap stay local, so
  the platform never sees that run as complete.

Accepting work, leases and attempt association use their own routes. A fault on
the evidence route does not stop new deliveries or protected actions that hold a
valid lease.

## Read the upload state

`GET /healthz` on a managed Runner carries a `managed_upload` block with counters
only, no run references:

| Field | Meaning |
|---|---|
| `streams_behind` | Runs with uploadable local events still waiting for platform confirmation. The platform is temporarily behind. |
| `streams_with_gap` | Runs held before a missing local sequence range. |
| `batches_acknowledged` / `batches_failed` / `batches_unconfirmed` | Batches confirmed by the platform's counts, failed at the transport or by rejection, or answered without a complete confirmation. |
| `gaps_detected` | Distinct local gaps found by this process. |

The counters reset when the Runner restarts.

A local gap is also written to the host's own evidence store, which operators
read from the local evidence files. Each record has kind `managed_upload_gap`,
principal `host`, outcome `gap`, the local run id as `ref`, and a `detail` holding
the task and run references plus `from_sequence` and `to_sequence`. The Runner
logs the same range once per process. If the evidence store cannot be written,
the Runner logs the failure and retries the record on the next cycle. Uploads of
other runs continue.

Treat the two signals differently:

- **Behind, no gap.** Check platform reachability and the Runner log for rejected
  or unconfirmed uploads. The backlog drains by itself once confirmations return.
- **Gap.** The run stays held until an operator reconciles it. Compare the
  recorded range with the run's local events and the platform projection, then
  decide from the business facts. The Runner does not skip the range, invent
  filler events or delete the retained events after it.

## The installed-Runner drill

`tests/scenarios/test_sc06_managed_evidence_upload.py` builds the Runner wheels,
installs them into a clean virtual environment and serves real platform deliveries
over loopback HTTP. A fault window in front of the evidence route only scripts
each upload attempt. Every delivery runs one protected write
(`submit_inventory_update`) plus a read, counted at the stub business API.

- **Before commit.** The route answers 503 before the projection runs. The write
  executes once, local action results and events stay readable, the platform
  projects nothing, and every refused batch restarts at the run's first event.
  After the window closes, the backlog arrives exactly once in batch order.
- **After commit.** Uploads are held until the run is complete locally, so the
  scripted batches are fixed. The projection then commits the first, second and
  terminal batches, but their answers are lost or truncated to `projected` only.
  Each resend carries the identical event ids, comes back as duplicates, and only
  then does the next batch start.
- **Process restart.** Once the run's facts are durable and an upload has been
  refused, the Runner is killed and restarted on the same profile and database.
  The new process retries from the first event. After recovery the platform
  holds every local event once under its host sequence, the business write count
  is unchanged and the delivery keeps its original local Task/Run references.
- **Run isolation.** One run is refused while another uploads. The refused run
  stays unprojected and its first confirmed batch still starts at its first
  event.
- **Local gap.** An isolated fixture drives an in-process channel against the
  same HTTP route over a host store missing a row inside one run. With one
  envelope per batch, the page after the prefix holds only the gap marker. The
  gap run uploads only its prefix, is reported with its range and recorded once
  per process, and survives a channel restart unchanged. A second run refused by
  the platform shows up as behind until it catches up.

Barriers are explicit. Each step waits on an observed fact, such as the local
terminal state in the Runner database, refused attempts seen at the route, the
platform rows or the health counters, rather than a fixed sleep.

## Evidence

Each test writes one JSON file to `HECATE_SC06_UPLOAD_EVIDENCE_DIR`, or to the
test's temporary directory by default. Files hold the commit, working-tree
status, source digests, both database tiers, the platform and Runner process
IDs, fault windows, and barriers. Each upload attempt includes
event IDs, sequences, event types, action and the platform's counts. The files
also record event associations, gap ranges and business call counts. Payloads,
connection strings, secrets and credentials are excluded.

## Run locally and on PostgreSQL

The default tier keeps the Runner database in a SQLite file. `uv` must be on
`PATH`; without it the installed-Runner tests skip and only the isolated gap
fixture runs.

```bash
export HECATE_SC06_UPLOAD_EVIDENCE_DIR=/tmp/sc06-upload-evidence
python scripts/dev.py test tests/scenarios/test_sc06_managed_evidence_upload.py -q -rA
```

`HECATE_STEP6_POSTGRES_URL` adds a PostgreSQL parametrization for the Runner
database, with a schema per test. The platform side of the drill stays on its
SQLite file, and the evidence records both tiers.

```bash
python scripts/dev.py sync --profile postgres
export HECATE_STEP6_POSTGRES_URL=postgresql+psycopg://hecate:hecate@localhost:5432/postgres
python scripts/dev.py test tests/scenarios/test_sc06_managed_evidence_upload.py \
  -q -rA --timeout=420 -p no:cacheprovider
```

The `step6-pg-process-matrix` CI job runs both tiers and keeps the JSON files as
the `sc06-managed-evidence-upload` artifact. Hosts that refuse the Runner
subprocess a connection to a containerized database cannot certify the
PostgreSQL tier locally; the Linux CI run is authoritative.

Channel-level regressions live in `packages/hecate-runner/tests/test_managed_upload.py`.
After changing shared Runner upload or platform projection code, also rerun the
SC04 and SC05 managed suites and `tests/test_execution/test_managed_execution_loop.py`.

## Verify local evidence failures

The standalone SC06 drill replaces the Runner's JSONL evidence directory with
a file to make evidence writes fail. It checks protected admission refusal,
reconciliation after a committed business write, and recovery of a queued
read-only submission. Replaying a request after recovery preserves the original
Task and Run and does not repeat completed business work.

```bash
export HECATE_SC06_EVIDENCE_FAILURE_DIR=/tmp/sc06-evidence-failure
python scripts/dev.py test tests/scenarios/test_sc06_evidence_failure.py -q -rA --timeout=420 -p no:cacheprovider
```

With the PostgreSQL profile and `HECATE_STEP6_POSTGRES_URL` set as above,
the command runs both task-store tiers. The evidence fault still targets JSONL
in both tiers. The `step6-pg-process-matrix` job retains the local drill reports
in the `sc06-evidence-failure` artifact.

## Not covered yet

- A backlog limit against the declared retention budget, and eviction of old
  local evidence.
- Platform-side visibility of a host gap. The platform only sees that the run has
  not finished projecting.
- Gaps at the end of a stream. The durable log only reports missing ranges
  between recorded events.
- Read-only continuation while local evidence is unavailable, and the complete
  SC06 scenario, which stays `planned` in `tests/scenarios/manifest.yaml`.
