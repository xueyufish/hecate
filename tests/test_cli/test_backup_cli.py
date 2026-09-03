"""Tests for CLI backup commands."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from hecate.cli.backup_cli import backup, restore


def test_cli_backup_create():
    """hecate backup create --scope=pg triggers backup."""
    runner = CliRunner()

    mock_record = MagicMock()
    mock_record.id = uuid.uuid4()
    mock_record.status = "completed"
    mock_record.error_message = None

    with patch("hecate.ops.backup.orchestrator.create_backup", new_callable=AsyncMock, return_value=mock_record):
        result = runner.invoke(backup, ["create", "--scope", "pg"])

    assert result.exit_code == 0
    assert "Backup complete" in result.output


def test_cli_backup_create_with_error():
    """hecate backup create reports errors."""
    runner = CliRunner()

    mock_record = MagicMock()
    mock_record.id = uuid.uuid4()
    mock_record.status = "failed"
    mock_record.error_message = "Connection refused"

    with patch("hecate.ops.backup.orchestrator.create_backup", new_callable=AsyncMock, return_value=mock_record):
        result = runner.invoke(backup, ["create"])

    assert result.exit_code == 0
    assert "Connection refused" in result.output


def test_cli_backup_list_empty():
    """hecate backup list shows 'No backups found' when empty."""
    runner = CliRunner()

    with patch("hecate.ops.backup.orchestrator.list_backups", new_callable=AsyncMock, return_value=[]):
        result = runner.invoke(backup, ["list"])

    assert result.exit_code == 0
    assert "No backups found" in result.output


def test_cli_backup_list_with_records():
    """hecate backup list displays backup records."""
    runner = CliRunner()

    mock_record = MagicMock()
    mock_record.id = uuid.uuid4()
    mock_record.started_at = datetime(2026, 7, 30, 2, 0, tzinfo=UTC)
    mock_record.scope = "all"
    mock_record.status = "completed"
    mock_record.size_bytes = 1048576

    with patch("hecate.ops.backup.orchestrator.list_backups", new_callable=AsyncMock, return_value=[mock_record]):
        result = runner.invoke(backup, ["list"])

    assert result.exit_code == 0
    assert "completed" in result.output


def test_cli_backup_verify():
    """hecate backup verify checks backup integrity."""
    runner = CliRunner()
    backup_id = uuid.uuid4()

    mock_result = {"matched": True, "mismatches": []}

    with patch("hecate.ops.backup.verification.verify_backup", new_callable=AsyncMock, return_value=mock_result):
        result = runner.invoke(backup, ["verify", str(backup_id)])

    assert result.exit_code == 0
    assert "PASSED" in result.output


def test_cli_backup_verify_failed():
    """hecate backup verify reports failures."""
    runner = CliRunner()
    backup_id = uuid.uuid4()

    mock_result = {"matched": False, "mismatches": ["agents: expected 10, got 8"]}

    with patch("hecate.ops.backup.verification.verify_backup", new_callable=AsyncMock, return_value=mock_result):
        result = runner.invoke(backup, ["verify", str(backup_id)])

    assert result.exit_code == 0
    assert "FAILED" in result.output


def test_cli_restore_confirms():
    """hecate restore requires user confirmation."""
    runner = CliRunner()
    backup_id = uuid.uuid4()

    mock_result = MagicMock()
    mock_result.status = "completed"
    mock_result.error = None

    with patch("hecate.ops.backup.restore.restore_backup", new_callable=AsyncMock, return_value=mock_result):
        result = runner.invoke(restore, [str(backup_id), "--yes"])

    assert result.exit_code == 0
    assert "Restore complete" in result.output


def test_cli_restore_aborted_without_confirm():
    """hecate restore can be aborted by user."""
    runner = CliRunner()
    backup_id = uuid.uuid4()

    result = runner.invoke(restore, [str(backup_id)], input="n\n")

    assert result.exit_code == 0
    assert "Aborted" in result.output


def test_cli_restore_with_conflict_replace():
    """hecate restore --conflict=replace passes strategy to engine."""
    runner = CliRunner()
    backup_id = uuid.uuid4()

    mock_result = MagicMock()
    mock_result.status = "completed"
    mock_result.error = None

    with patch(
        "hecate.ops.backup.restore.restore_backup", new_callable=AsyncMock, return_value=mock_result
    ) as mock_restore:
        result = runner.invoke(
            restore,
            [str(backup_id), "--conflict", "replace", "--yes"],
        )

    assert result.exit_code == 0
    call_kwargs = mock_restore.call_args.kwargs
    assert call_kwargs.get("conflict") == "replace"
