"""Backfill real embeddings for L3 + L4 memory rows still carrying mock vectors.

The memory-importance-fusion change introduced the ``embedding_real`` column;
legacy rows (REST writes that ran before the change) hold a deterministic md5
hash vector that the ranking layer explicitly excludes from cosine scoring.
This script replaces each pending row's ``embedding`` with a real model
embedding and flips ``embedding_real=True``.

Idempotent by construction: only rows where ``embedding_real=False`` are
processed. Already-real rows are skipped. Embedding failures leave the row
untouched and logged (the row will be retried on the next invocation).

Bounded batch size keeps DB pressure predictable; ``--limit`` caps the run
for cron windows. Re-run as needed until ``0 remaining`` is reported.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from sqlalchemy import select, update

from hecate.core.database import async_session_factory
from hecate.models.memory import KnowledgeMemoryModel, MemoryModel

logger = logging.getLogger("backfill_embeddings")


async def backfill(*, batch_size: int, max_rows: int | None) -> None:
    """One idempotent pass: re-embed pending rows across both memory layers."""
    try:
        from hecate_memory.rag.embedding import embedding_service
    except ImportError:
        logger.error("hecate-memory not installed; cannot backfill")
        sys.exit(2)
    if embedding_service.is_mock:
        logger.error("Embedding service is in mock mode; install FlagEmbedding to enable backfill")
        sys.exit(2)

    processed = 0
    failed = 0
    async with async_session_factory() as db:
        for model in (MemoryModel, KnowledgeMemoryModel):
            stmt = (
                select(model)
                .where(model.embedding_raw == False, ~model.deleted)  # noqa: E712
                .limit(batch_size)
            )
            rows = (await db.execute(stmt)).scalars().all()
            for row in rows:
                if max_rows is not None and processed >= max_rows:
                    break
                try:
                    result = await embedding_service.encode_query(row.content)
                    await db.execute(
                        update(model).where(model.id == row.id).values(embedding=result.dense, embedding_real=True)
                    )
                    processed += 1
                except Exception as e:  # noqa: BLE001
                    failed += 1
                    logger.warning("Backfill failed for %s/%s: %s", model.__tablename__, row.id, e)
            await db.commit()
    logger.info("Backfill pass complete: %d processed, %d failed", processed, failed)
    if failed:
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=100, help="Rows per commit (default 100)")
    parser.add_argument("--limit", type=int, default=None, help="Max rows to process in this run")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(backfill(batch_size=args.batch_size, max_rows=args.limit))


if __name__ == "__main__":
    main()
