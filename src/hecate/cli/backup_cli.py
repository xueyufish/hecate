"""CLI commands for backup and restore operations."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime

import click


@click.group()
def backup() -> None:
    """Backup management commands."""


@backup.command()
@click.option("--scope", default="all", help="Backup scope: all, pg, qdrant, minio, fs")
def create(scope: str) -> None:
    """Create a new backup."""
    from hecate.ops.backup.orchestrator import create_backup

    click.echo(f"Creating backup (scope={scope})...")
    record = asyncio.run(create_backup(scope=scope))
    click.echo(f"Backup complete: id={record.id}, status={record.status}")
    if record.error_message:
        click.echo(f"Errors: {record.error_message}", err=True)


@backup.command("list")
@click.option("--status", default=None, help="Filter by status")
@click.option("--limit", default=50, help="Maximum number of records")
def list_backups(status: str | None, limit: int) -> None:
    """List backup records."""
    from hecate.ops.backup.orchestrator import list_backups as _list

    records = asyncio.run(_list(status=status, limit=limit))
    if not records:
        click.echo("No backups found.")
        return
    for r in records:
        click.echo(
            f"  {r.id}  {r.started_at:%Y-%m-%d %H:%M}  "
            f"scope={r.scope}  status={r.status}  "
            f"size={r.size_bytes or 0} bytes"
        )


@backup.command()
@click.argument("backup_id", type=uuid.UUID)
def verify(backup_id: uuid.UUID) -> None:
    """Verify a backup's integrity."""
    from hecate.ops.backup.verification import verify_backup

    click.echo(f"Verifying backup {backup_id}...")
    result = asyncio.run(verify_backup(backup_id))
    if result.get("matched"):
        click.echo("Verification PASSED: all row counts match")
    else:
        click.echo("Verification FAILED:", err=True)
        for m in result.get("mismatches", []):
            click.echo(f"  {m}", err=True)


@backup.command()
@click.option("--before", required=True, help="Delete backups before this date (YYYY-MM-DD)")
def cleanup(before: str) -> None:
    """Delete backups older than the specified date."""

    from sqlalchemy import delete as sa_delete

    from hecate.core.database import async_session_factory
    from hecate.models.backup import BackupRecordModel

    before_date = datetime.fromisoformat(before).date()

    async def _cleanup():
        async with async_session_factory() as session:
            stmt = sa_delete(BackupRecordModel).where(BackupRecordModel.started_at < before_date)
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount

    count = asyncio.run(_cleanup())
    click.echo(f"Deleted {count} backup records before {before_date}")


@click.command()
@click.argument("backup_id", type=uuid.UUID)
@click.option("--scope", default="all", help="Restore scope: all, pg, qdrant, minio, fs")
@click.option("--workspace", type=uuid.UUID, default=None, help="Restore only this workspace")
@click.option(
    "--conflict",
    type=click.Choice(["replace", "merge", "fail"]),
    default="fail",
    help="Conflict strategy when target data exists",
)
@click.option("--pitrs", default=None, help="PITR timestamp (ISO 8601)")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt")
def restore(
    backup_id: uuid.UUID,
    scope: str,
    workspace: uuid.UUID | None,
    conflict: str,
    pitrs: str | None,
    yes: bool,
) -> None:
    """Restore data from a backup."""
    from hecate.ops.backup.restore import restore_backup

    if not yes:
        click.echo(f"WARNING: This will restore backup {backup_id}")
        click.echo(f"  Scope: {scope}")
        if workspace:
            click.echo(f"  Workspace: {workspace}")
        click.echo(f"  Conflict strategy: {conflict}")
        if pitrs:
            click.echo(f"  PITR target: {pitrs}")
        click.echo()
        if not click.confirm("Proceed with restore?"):
            click.echo("Aborted.")
            return

    pitr_ts = datetime.fromisoformat(pitrs) if pitrs else None

    result = asyncio.run(
        restore_backup(
            backup_id=backup_id,
            scope=scope,
            workspace_id=workspace,
            conflict=conflict,
            pitr_timestamp=pitr_ts,
        )
    )
    click.echo(f"Restore complete: status={result.status}")
    if result.error:
        click.echo(f"Error: {result.error}", err=True)
