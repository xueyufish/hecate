"""Persist confirmed and in-flight model usage, then die without cleanup."""

from __future__ import annotations

import asyncio
import os
import sys


async def main() -> None:
    """Leave both usage categories durable across an abrupt process exit."""
    from hecate_durable.storage import SqlDurableStore
    from hecate_governance.usage import UsageRecord, UsageStatus

    from hecate.execution.agent_child import DurableSliceLedger
    from hecate.execution.task_control import task_ref_of

    url, child_id = sys.argv[1:]
    store = SqlDurableStore(url, source="platform")
    ledger = DurableSliceLedger(store=store, task_ref=task_ref_of(child_id), allocation=10)
    await ledger.reserve("confirmed", 5)
    await ledger.settle(UsageRecord("confirmed", 2, UsageStatus.ACTUAL))
    await ledger.reserve("in-flight", 6)
    os._exit(73)


if __name__ == "__main__":
    asyncio.run(main())
