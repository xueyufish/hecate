# Read task results through the REST result inbox

A JWT user can submit a platform-local top-level Controller task and
retrieve its results after disconnecting. The inbox is scoped to one
Task, its original Run and the authenticated submitter.

## Submit and resume

Send `result_delivery: "rest_inbox"` in the `POST /api/tasks` body with
`input.controller_loop: true`, `wait: false` and `stream: false`. The
server persists the source binding with the submission. An identical
idempotency replay returns the original Task and Run. Changing the
delivery option under the same key returns `409`. The option requires
a JWT user and a platform-local top-level Controller task.

Read `GET /api/tasks/{task_id}/result-inbox` with the same JWT identity.
Each read checks current workspace membership, the VIEWER floor, the
original submitter and the visibility of the Task and its bound Run.
Unavailable inboxes return `404`. The cursor is scoped to that Run.

Each response contains `notifications`, `next_cursor` and `has_more`.
Process and save the page before advancing your checkpoint. An empty
page can advance the cursor past progress events. Drain while
`has_more` is true and poll from the returned cursor afterward.
Retry a failed request with your last saved cursor. Re-reading can
return the same notice ID, so store notices by ID.

## Notification vocabulary

| Type | Category | Meaning | View |
|---|---|---|---|
| `result_ready` | `result` | The Run delivery projection is committed | Run detail |
| `task_stopped` | `stop` | The Task failed or was cancelled | Task and Run detail |
| `approval_required` | `needs_attention` | Approval history, open while the current authority is pending | Task approval |
| `input_required` | `needs_attention` | Current, unconsumed human input wait | Task detail and commands |
| `reconciliation_required` | `needs_attention` | A recorded reconciliation requirement with its current status | Pending reconciliation |

Successful delivery uses the committed Run terminal projection. Failed
or cancelled delivery uses the Task's recorded transition. Both carry
one task-keyed completion ID. The normal outbox worker advances
visibility. Inbox reads perform no task operations.

A notice contains its identity, state, Task/Run references and view
links. Retrieve the result preview and verified acceptance view through
the existing authorized Run detail endpoint. To refresh a pending-work
display, rebuild it from a fresh scan and replace the old snapshot.
A resolved input wait disappears, while approval history can report
`resolved`. The linked control views enforce their own action permissions.

## Live input and worker restarts

A running platform-local top-level Controller task can also accept
in-flight input through `POST /api/tasks/{task_id}/commands` with
`payload_schema_ref: "urn:hecate:task-live-input/1"` while a model call
is still executing. The input binds at the next decision that has yet
to start. If the worker dies after binding and before consumption, a
replacement worker resumes the same Task and Run at the bound decision.
The drill verifies that the revision is consumed once and previously
confirmed tool calls keep their original receipts.

The drill replays the input command across the restart and checks the
original submitter's saved cursor before and after terminal projection.
The notice becomes visible after projection. The Run detail includes
the structured result with the supplemented input and the recorded
acceptance checks for output shape and content. Inbox reads require
the original submitter's current workspace access.

```bash
python scripts/dev.py test tests/test_execution/test_live_input_restart.py \
  -k rest_inbox_delivery_survives_worker_restart -q
```

The drill keeps model and tool outputs deterministic and the HTTP, JWT,
command, dispatch, worker-process, decision and SQL commit paths real.
It runs on SQLite by default. For the PostgreSQL profile and evidence
configuration, see [cross-process recovery acceptance](handle-task-input-waits.md#cross-process-recovery-acceptance).

## Copyable REST client

Set `HECATE_JWT`, `HECATE_AGENT_ID` and `HECATE_REQUEST_KEY`. Use a
separate checkpoint path for each request. The example keeps the cursor
and merged notices in one local checkpoint so a new process can resume.

```python
import json
import os
import time
from pathlib import Path

import httpx

BASE = os.environ.get("HECATE_URL", "http://127.0.0.1:8000")
CHECKPOINT = Path(os.environ.get("HECATE_INBOX_CHECKPOINT", "result-inbox.json"))
HEADERS = {"Authorization": f"Bearer {os.environ['HECATE_JWT']}"}


def save(state: dict) -> None:
    temp = CHECKPOINT.with_suffix(".tmp")
    temp.write_text(json.dumps(state), encoding="utf-8")
    temp.replace(CHECKPOINT)


def submit() -> dict:
    with httpx.Client(base_url=BASE, headers=HEADERS, timeout=15) as client:
        response = client.post(
            "/api/tasks",
            json={
                "goal": "Prepare the inventory summary",
                "agent_id": os.environ["HECATE_AGENT_ID"],
                "input": {
                    "controller_loop": True,
                    "messages": [{"role": "user", "content": "Summarize current stock."}],
                },
                "idempotency_key": os.environ["HECATE_REQUEST_KEY"],
                "result_delivery": "rest_inbox",
            },
        )
        response.raise_for_status()
        return {"task_id": response.json()["task_id"], "cursor": None, "notices": {}}


def main() -> None:
    state = json.loads(CHECKPOINT.read_text(encoding="utf-8")) if CHECKPOINT.exists() else submit()
    save(state)
    while True:
        params = {"limit": 50}
        if state["cursor"] is not None:
            params["cursor"] = state["cursor"]
        with httpx.Client(base_url=BASE, headers=HEADERS, timeout=15) as client:
            response = client.get(f"/api/tasks/{state['task_id']}/result-inbox", params=params)
            response.raise_for_status()
            page = response.json()
            for note in page["notifications"]:
                state["notices"][note["notification_id"]] = note
            state["cursor"] = page["next_cursor"]
            save(state)

            for note in page["notifications"]:
                if note.get("status") == "open":
                    print(note["type"], note["views"])
            completion = state["notices"].get(f"completion:{state['task_id']}")
            if completion and completion["type"] == "task_stopped":
                print("Task stopped", completion["state"])
                return
            if completion:
                result = client.get(completion["views"]["run_detail"])
                result.raise_for_status()
                run = result.json()
                print(run["projection"]["result_preview"])
                print(run.get("delivery_acceptance"))
                return
        if not page["has_more"]:
            time.sleep(2.0)


if __name__ == "__main__":
    main()
```

## Verify the profile

```bash
python scripts/dev.py test tests/test_api/test_task_result_inbox.py -q
```

The tests exercise SQLite projection recovery and a loopback TCP client
with real JWT checks, deterministic model and tool output, and the
production Task/Run persistence path. Set `HECATE_INBOX_EVIDENCE_DIR` to
retain the drill evidence with the checkout SHA and source digests.

## Verify trusted-field delivery

The same test file closes the loop with a host-declared field provenance
profile on the agent version (the `tests/scenarios/baseline/` inventory
tools, real dispatch, production runtime port and guard). The drills
use loopback TCP, SQLite, JWT identities and a deterministic model.

```bash
python scripts/dev.py test tests/test_api/test_task_result_inbox.py -k trusted -q
```

Each successful drill writes a separate `trusted-field-inbox-*.json`
record to `HECATE_INBOX_EVIDENCE_DIR` when configured.

1. The model sources the protected `order_ref` from the same resource's
   read receipt. The write lands exactly once. The reconnecting submitter
   reads `result_ready`, follows the run-detail link and sees
   `delivery_acceptance.status == "passed"`.
2. A guessed value is intercepted by the real guard (zero business
   writes) and surfaces as an open `input_required` notification. An EDITOR
   wakes the same Task/Run with one idempotent `provide_input` command
   (the same `command_id` replays the settled receipt). After one authorized
   write, the resumed cursor reads the unique completion. A full re-read
   drops the old input notification.
3. A same-workspace non-submitter's inbox read returns `404`. A VIEWER's
   input command returns `403`. A revoked submitter's saved-cursor read
   returns `401`. Inbox pages expose only notification metadata and
   authorized view links.

Run the related regressions with the following command.

```bash
python scripts/dev.py test tests/test_api/test_task_command_access_boundary.py \
  tests/test_execution/test_field_provenance.py \
  tests/test_execution/test_delivery_acceptance_dispatch.py \
  tests/scenarios/test_controller_trusted_field.py -q
```
