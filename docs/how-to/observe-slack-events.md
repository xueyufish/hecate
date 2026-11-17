# Observe a signed Slack event source

Use a dedicated Slack App to collect event receipt evidence for P-C.
The callback records observations and refusal counts. It creates zero
Tasks and Runs and enqueues zero IM messages. The chat integration uses
its existing Request URL and credentials.

## Configure the observation entry

The entry is disabled by default. It supports PostgreSQL and file-backed
SQLite. Apply the platform migrations, then provision one existing
Hecate workspace, one Slack App installed in one Slack team, and one
channel in that team. Use a channel local to the team.

```dotenv
SLACK_EVENT_OBSERVATION_ENABLED=false
SLACK_EVENT_OBSERVATION_SIGNING_SECRET=
SLACK_EVENT_OBSERVATION_APP_ID=
SLACK_EVENT_OBSERVATION_TEAM_ID=
SLACK_EVENT_OBSERVATION_CHANNEL_ID=
SLACK_EVENT_OBSERVATION_WORKSPACE_ID=
SLACK_EVENT_OBSERVATION_RETENTION_DAYS=30
SLACK_EVENT_OBSERVATION_REJECT_LOG_LIMIT=500
SLACK_EVENT_OBSERVATION_REJECT_LOG_WINDOW_SECONDS=3600
SLACK_EVENT_OBSERVATION_MAX_BODY_BYTES=65536
```

Set the dedicated App's signing secret and the source bindings, enable
the entry, and restart the platform processes. Set the App's Request URL
to `https://<your-host>/v1/slack-events`. Slack's
[`url_verification` request](https://docs.slack.dev/reference/events/url_verification/)
carries a challenge. The entry verifies the dedicated App's signature
before echoing the challenge. The challenge creates no ledger row.

Subscribe to [`app_mention`](https://docs.slack.dev/reference/events/app_mention/).
For event callbacks the entry verifies the signature and timestamp, then
checks the App, team, channel and event type. It drops bot events and
events marked as shared across Slack teams. Missing or stale signatures
receive 401 when the profile is configured. Point the deployment's
ingress rate and connection limits at the public callback, and allow
requests up to the configured body size.

To stop collection, turn off the Slack App's subscription, set
`SLACK_EVENT_OBSERVATION_ENABLED=false`, and restart all platform
processes. The POST returns 404 when disabled. The bound workspace
administrator can still read the retained summary.

## Receipt, deduplication and retention

An accepted callback receives 200 after PostgreSQL or SQLite commits the
observation. Only an existing workspace/source/event key produces a
`duplicate` receipt. A failed write produces 500 for retry. Both a later
event and a late but previously unseen event can be recorded. The entry
uses receipt time for retention.

Rows for observed events contain the verified app/team/channel
identifiers, Slack's event ID and type, the Hecate workspace, receipt time
and disposition. The body, attachments, headers and secrets are excluded
from storage. The callback uses the observation ledger for evidence and
does not emit per-request rows to the general audit stream. Reads of the
authenticated summary are audited normally.

Signed refusals increment one counter per workspace, fixed reason and
time window. Each counter saturates at
`SLACK_EVENT_OBSERVATION_REJECT_LOG_LIMIT`. Set it to zero to disable
refusal counters. Forged or stale signatures and oversized bodies produce
no ledger entries. Duplicate accepted events produce no additional
entries. Summary counts report stored observations and capped refusal
counts. The latest timestamp describes the latest counted receipt.

Schedule the retention sweep using a deployment-owned job. It deletes
rows older than `SLACK_EVENT_OBSERVATION_RETENTION_DAYS`, including their
dedup keys. Keep the window longer than the deployment's expected delivery
retries. Leave the table in place when disabling collection. The migration
refuses to drop a ledger that still contains evidence.

```python
from hecate.channel.api.v1.slack_events import purge_expired_observations

removed = await purge_expired_observations(session)
```

## Verify collection

Use a JWT with a current ADMIN membership in the bound Hecate workspace
to read `GET /v1/slack-events/observations/summary`. The reply contains
counts and the latest recorded receipt time for that workspace.

Run the synthetic regression suite locally.

```bash
python scripts/dev.py test tests/test_api/test_slack_event_observation.py \
  tests/test_api/test_channels_webhook.py tests/test_channels/test_slack_channel.py -q -rs
```

For the PostgreSQL drill, set `DATABASE_URL` to an empty scratch
PostgreSQL database and run `python -m alembic upgrade head`. Set
`HECATE_SLACK_OBSERVATION_TEST_POSTGRES` to that same database URL and
rerun the observation suite. The drill uses the mounted platform app
over loopback TCP and verifies concurrent redeliveries, the refusal cap,
the same receipt from a separately started platform process, and
zero Task, Run, IM enqueue and general audit records from the callback.
Use synthetic signing keys. Live Slack enrollment requires an authorized
test App.
