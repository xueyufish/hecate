"""Persist model usage then die, probe the surviving slice, or hammer it concurrently.

``python -m tests.test_execution._agent_child_budget_process <child-id> <mode> ...``
with the database bound through the environment (see ``_agent_child_db``):

``die``                 reserve a confirmed attempt, settle it, reserve an in-flight
                        attempt and exit without cleanup (exit code 73) — the
                        in-flight hold must stay an unknown charge.
``probe``               open the slice from an independent interpreter and report the
                        durable summary plus whether a retry exceeding the remaining
                        allocation is refused BEFORE any model request.
``hammer <prefix> <n>`` reserve+settle ``n`` distinct one-unit attempts as fast as
                        possible; report every key this process saw succeed so the
                        test can prove none was lost to a concurrent writer.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path


async def main() -> None:
    from hecate_governance.usage import ReservationStatus, UsageRecord, UsageStatus

    from hecate.execution.agent_child import DurableSliceLedger
    from hecate.execution.task_control import task_ref_of
    from tests.test_execution._agent_child_db import backend_name, open_store_and_sessions

    child_id, mode = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "die")
    allocation = int(os.environ.get("HECATE_AGENT_CHILD_SLICE_ALLOCATION") or 10)
    store, engine, _sessions = open_store_and_sessions()
    ledger = DurableSliceLedger(store=store, task_ref=task_ref_of(child_id), allocation=allocation)
    if mode == "die":
        await ledger.reserve("confirmed", 5)
        await ledger.settle(UsageRecord("confirmed", 2, UsageStatus.ACTUAL))
        await ledger.reserve("in-flight", 6)
        os._exit(73)
    try:
        if mode == "probe":
            summary = await ledger.snapshot()
            retry = await ledger.reserve("retry", 3)
            report = {
                "backend": backend_name(),
                "used": summary["used"],
                "unknown_count": summary["unknown_count"],
                "retry_status": retry.status.value,
                "remaining": await ledger.remaining(),
            }
        elif mode == "hammer":
            prefix, count = sys.argv[3], int(sys.argv[4])
            barrier = os.environ.get("HECATE_AGENT_CHILD_CAS_BARRIER")
            initial_revision = None
            if barrier:
                original_load = ledger._load

                async def load_together():
                    nonlocal initial_revision
                    state = await original_load()
                    if initial_revision is None:
                        initial_revision = state["_revision"]
                        root = Path(barrier)
                        (root / f"ready-{os.getpid()}").touch()
                        end = asyncio.get_running_loop().time() + 60
                        while len(list(root.glob("ready-*"))) < 2:
                            if asyncio.get_running_loop().time() >= end:
                                raise TimeoutError("second slice writer did not reach the read barrier")
                            await asyncio.sleep(0.01)
                    return state

                ledger._load = load_together
            settled: list[str] = []
            settle_failed: list[str] = []  # reserved durably, then the settle lost every CAS retry
            reserve_failed: list[str] = []  # never persisted: no entry may exist for these
            refused = 0
            errors: list[str] = []
            for index in range(count):
                key = f"{prefix}-{index}"
                try:
                    outcome = await ledger.reserve(key, 1)
                except Exception as exc:  # noqa: BLE001 — the test judges the durable outcome
                    errors.append(f"{key}: reserve {type(exc).__name__}: {exc}")
                    reserve_failed.append(key)
                    continue
                if outcome.status is not ReservationStatus.RESERVED:
                    refused += 1
                    continue
                try:
                    await ledger.settle(UsageRecord(key, 1, UsageStatus.ACTUAL))
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{key}: settle {type(exc).__name__}: {exc}")
                    settle_failed.append(key)
                    continue
                settled.append(key)
            report = {
                "backend": backend_name(),
                "pid": os.getpid(),
                "settled": settled,
                "settle_failed": settle_failed,
                "reserve_failed": reserve_failed,
                "refused": refused,
                "errors": errors,
                "initial_revision": initial_revision,
            }
        else:
            raise SystemExit(f"unknown mode {mode!r}")
        print(json.dumps(report))
    finally:
        await engine.dispose()
        store.dispose()


if __name__ == "__main__":
    asyncio.run(main())
