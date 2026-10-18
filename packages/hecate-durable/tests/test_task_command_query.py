"""Command query parity over InMemory and isolated SQLite/PostgreSQL stores."""

from __future__ import annotations

import os

import pytest
from _decision_helpers import decision_database
from hecate_durable.contracts.durable import CommandState, ControlCommandKind, ControlCommandRecord
from hecate_durable.contracts.references import BackendRef, RefKind, task_ref
from hecate_durable.stub import InMemoryControlCommandRecorder

COMMAND_STATES = list(CommandState)
BACKENDS = ["stub", "sqlite"] + (["postgres"] if os.environ.get("DURABLE_TEST_POSTGRES_URL") else [])


@pytest.fixture(params=BACKENDS)
def recorder(request, tmp_path):
    if request.param == "stub":
        yield InMemoryControlCommandRecorder()
    else:
        url = os.environ["DURABLE_TEST_POSTGRES_URL"] if request.param == "postgres" else ""
        with decision_database(tmp_path, url) as (store, _clock, _url):
            yield store


def _command(
    command_id: str,
    state: CommandState,
    *,
    issuer: str = "hecate",
    task_id: str = "task-1",
    workspace: str = "ws-1",
    issued_at: str = "2026-10-18T10:00:00+00:00",
    expires_at: str | None = None,
) -> ControlCommandRecord:
    return ControlCommandRecord(
        command_id=command_id,
        kind=ControlCommandKind.CANCEL,
        issuer="user-1",
        task_ref=BackendRef(RefKind.TASK, issuer, task_id),
        issued_at=issued_at,
        expires_at=expires_at,
        state=state,
        extra={"workspace_id": workspace},
    )


def test_five_states_returned_verbatim(recorder) -> None:
    for index, state in enumerate(COMMAND_STATES):
        recorder.record(_command(f"cmd-{index}", state))
    items, total = recorder.list_task_commands("ws-1", task_ref("hecate", "task-1"), 10, 0)
    assert total == 5
    assert sorted(item.state for item in items) == sorted(COMMAND_STATES)


def test_order_is_issued_at_desc_with_command_id_tiebreak(recorder) -> None:
    recorder.record(_command("cmd-a", CommandState.REQUESTED, issued_at="2026-10-18T09:00:00+00:00"))
    recorder.record(_command("cmd-b", CommandState.ACKNOWLEDGED, issued_at="2026-10-18T10:00:00+00:00"))
    recorder.record(_command("cmd-c", CommandState.APPLIED, issued_at="2026-10-18T10:00:00+00:00"))
    items, _ = recorder.list_task_commands("ws-1", task_ref("hecate", "task-1"), 10, 0)
    assert [item.command_id for item in items] == ["cmd-c", "cmd-b", "cmd-a"]


def test_pagination_stitches_without_duplicates(recorder) -> None:
    for index in range(5):
        recorder.record(_command(f"cmd-{index}", CommandState.REQUESTED, issued_at=f"2026-10-18T0{index}:00:00+00:00"))
    seen: list[str] = []
    for offset in range(5):
        items, total = recorder.list_task_commands("ws-1", task_ref("hecate", "task-1"), 1, offset)
        assert total == 5
        seen.extend(item.command_id for item in items)
    assert len(seen) == len(set(seen)) == 5
    items, total = recorder.list_task_commands("ws-1", task_ref("hecate", "task-1"), 2, 10)
    assert (items, total) == ([], 5)


def test_full_task_reference_scopes_not_task_id_alone(recorder) -> None:
    recorder.record(_command("cmd-mine", CommandState.REQUESTED))
    recorder.record(_command("cmd-foreign", CommandState.REQUESTED, issuer="foreign.host", task_id="task-1"))
    items, total = recorder.list_task_commands("ws-1", task_ref("hecate", "task-1"), 10, 0)
    assert [item.command_id for item in items] == ["cmd-mine"]
    assert total == 1


def test_cross_workspace_isolated(recorder) -> None:
    recorder.record(_command("cmd-here", CommandState.REQUESTED))
    recorder.record(_command("cmd-there", CommandState.REQUESTED, workspace="ws-2"))
    items, total = recorder.list_task_commands("ws-1", task_ref("hecate", "task-1"), 10, 0)
    assert [item.command_id for item in items] == ["cmd-here"]
    assert total == 1


def test_expired_requested_stays_requested_in_list(recorder) -> None:
    recorder.record(_command("cmd-lapsed", CommandState.REQUESTED, expires_at="2000-01-01T00:00:00+00:00"))
    items, _ = recorder.list_task_commands("ws-1", task_ref("hecate", "task-1"), 10, 0)
    assert items[0].state is CommandState.REQUESTED
    # The list itself never mutated the stored receipt.
    stored = recorder.get("cmd-lapsed")
    assert stored is not None and stored.state is CommandState.REQUESTED


def test_empty_result_is_valid(recorder) -> None:
    assert recorder.list_task_commands("ws-1", task_ref("hecate", "task-1"), 10, 0) == ([], 0)


@pytest.mark.parametrize(("limit", "offset"), [(0, 0), (-1, 0), (101, 0), (1, -1)])
def test_query_rejects_invalid_pagination_consistently(recorder, limit, offset):
    with pytest.raises(ValueError, match="pagination"):
        recorder.list_task_commands("ws-1", task_ref("hecate", "task-1"), limit, offset)


def test_query_rejects_non_task_reference(recorder):
    with pytest.raises(ValueError, match="task"):
        recorder.list_task_commands("ws-1", BackendRef(RefKind.RUN, "hecate", "task-1"), 10, 0)
