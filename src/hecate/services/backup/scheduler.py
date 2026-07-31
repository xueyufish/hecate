"""Backup scheduling and retention management via APScheduler."""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from hecate.core.config import settings
from hecate.core.database import async_session_factory
from hecate.models.backup import BackupRecordModel, BackupScope, BackupStatus

from .orchestrator import create_backup

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
    return _scheduler


def start_backup_scheduler() -> None:
    """Register scheduled backup jobs if BACKUP_SCHEDULE_ENABLED is true."""
    if not settings.BACKUP_SCHEDULE_ENABLED:
        logger.info("Backup scheduling is disabled")
        return

    scheduler = get_scheduler()
    trigger = CronTrigger.from_crontab(settings.BACKUP_SCHEDULE_CRON)

    scheduler.add_job(
        _scheduled_backup,
        trigger=trigger,
        id="hecate-backup-full",
        replace_existing=True,
    )
    logger.info("Scheduled full backup: %s", settings.BACKUP_SCHEDULE_CRON)

    if settings.BACKUP_VERIFY_ENABLED:
        verify_trigger = CronTrigger.from_crontab(settings.BACKUP_VERIFY_SCHEDULE)
        scheduler.add_job(
            _scheduled_verify,
            trigger=verify_trigger,
            id="hecate-backup-verify",
            replace_existing=True,
        )
        logger.info("Scheduled backup verification: %s", settings.BACKUP_VERIFY_SCHEDULE)

    if not scheduler.running:
        scheduler.start()


async def _scheduled_backup() -> None:
    """Run a scheduled full backup."""
    logger.info("Starting scheduled backup")
    try:
        await create_backup(scope=BackupScope.ALL)
    except Exception as e:
        logger.error("Scheduled backup failed: %s", e)


async def _scheduled_verify() -> None:
    """Run a scheduled verification of the latest backup."""
    from .verification import verify_backup

    async with async_session_factory() as session:
        stmt = (
            select(BackupRecordModel)
            .where(BackupRecordModel.status == BackupStatus.COMPLETED)
            .order_by(BackupRecordModel.started_at.desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        record = result.scalars().first()

    if record:
        try:
            await verify_backup(record.id)
        except Exception as e:
            logger.error("Scheduled verification failed: %s", e)
    else:
        logger.warning("No completed backup found to verify")


async def apply_retention_policy() -> int:
    """Delete backups exceeding the retention policy. Returns count deleted."""
    deleted_count = 0

    async with async_session_factory() as session:
        for _backup_type, max_count in [
            ("hourly", settings.BACKUP_RETENTION_HOURLY),
            ("daily", settings.BACKUP_RETENTION_DAILY),
            ("monthly", settings.BACKUP_RETENTION_MONTHLY),
        ]:
            stmt = (
                select(BackupRecordModel)
                .where(BackupRecordModel.status.in_([BackupStatus.COMPLETED, BackupStatus.PARTIAL]))
                .order_by(BackupRecordModel.started_at.desc())
            )
            result = await session.execute(stmt)
            records = list(result.scalars().all())

            if len(records) <= max_count:
                continue

            for record in records[max_count:]:
                await session.delete(record)
                deleted_count += 1

        await session.commit()

    if deleted_count:
        logger.info("Retention policy deleted %d old backups", deleted_count)
    return deleted_count
