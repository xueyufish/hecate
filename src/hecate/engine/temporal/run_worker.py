"""Temporal worker startup script.

Starts a Temporal worker that polls the configured task queue and
executes graph node Activities. Run with: python -m hecate.engine.temporal.run_worker
"""

from __future__ import annotations

import asyncio
import logging
import sys

from hecate.core.config import Settings

logger = logging.getLogger(__name__)


async def main() -> None:
    """Start the Temporal worker process."""
    settings = Settings()

    logger.info(f"Connecting to Temporal server at {settings.TEMPORAL_SERVER_URL}")
    logger.info(f"Task queue: {settings.TEMPORAL_TASK_QUEUE}")

    try:
        from temporalio.client import Client
        from temporalio.worker import Worker
    except ImportError as err:
        raise ImportError(
            "temporalio is required for Temporal worker. Install with: pip install hecate[temporal]"
        ) from err

    client = await Client.connect(settings.TEMPORAL_SERVER_URL)

    worker = Worker(
        client=client,
        task_queue=settings.TEMPORAL_TASK_QUEUE,
        activities=[],
    )

    logger.info("Temporal worker started. Press Ctrl+C to stop.")
    await worker.run()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Worker stopped.")
        sys.exit(0)
    except ImportError as e:
        logger.error(str(e))
        sys.exit(1)
