"""Tests for memory-lifecycle-governance schema additions.

Covers the new ORM surface added by the change:

- ``MemoryPolicyModel`` — one row per (workspace, agent) scope via the
  zero-UUID sentinel for the workspace level; duplicate scope rejected.
- ``ConsolidationFlushWindowModel`` — pending flush window rows.
- ``archived_at`` lifecycle soft-delete marker on L3/L4 rows (null default,
  settable, does not touch ``deleted``).
- ``memory_edit_log.reason`` — lifecycle audit cause column.
- Platform config defaults for the two new flags (off) and lifecycle caps.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from hecate.core.config import settings
from hecate.models.memory import (
    ConsolidationFlushWindowModel,
    KnowledgeMemoryModel,
    MemoryEditLogModel,
    MemoryModel,
    MemoryPolicyModel,
)

_ZERO = uuid.UUID("00000000-0000-0000-0000-000000000000")
_WS = uuid.uuid4()


async def test_memory_policy_workspace_scope_sentinel(db_session):
    """agent_id = zero UUID is the workspace-level policy row."""
    db_session.add(MemoryPolicyModel(workspace_id=_WS, agent_id=_ZERO))
    await db_session.flush()

    rows = (await db_session.execute(select(MemoryPolicyModel).where(~MemoryPolicyModel.deleted))).scalars().all()
    assert len(rows) == 1
    assert rows[0].agent_id == _ZERO
    assert rows[0].enabled is True
    assert rows[0].tool_subset is None
    assert rows[0].params is None


async def test_memory_policy_duplicate_scope_rejected(db_session):
    """A second row for the same (workspace, agent) scope is rejected."""
    db_session.add(MemoryPolicyModel(workspace_id=_WS, agent_id=_ZERO))
    await db_session.flush()
    db_session.add(MemoryPolicyModel(workspace_id=_WS, agent_id=_ZERO))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_memory_policy_agent_scope_distinct_from_workspace(db_session):
    """Different agent scopes under one workspace coexist."""
    db_session.add(MemoryPolicyModel(workspace_id=_WS, agent_id=_ZERO))
    db_session.add(MemoryPolicyModel(workspace_id=_WS, agent_id=uuid.uuid4()))
    await db_session.flush()

    rows = (await db_session.execute(select(MemoryPolicyModel).where(~MemoryPolicyModel.deleted))).scalars().all()
    assert len(rows) == 2


async def test_flush_window_row_roundtrip(db_session):
    now = datetime.now(UTC)
    db_session.add(
        ConsolidationFlushWindowModel(
            workspace_id=_WS,
            agent_id=uuid.uuid4(),
            user_id=_ZERO,
            window_start=now,
            window_end=now,
        )
    )
    await db_session.flush()

    row = (
        (await db_session.execute(select(ConsolidationFlushWindowModel).where(~ConsolidationFlushWindowModel.deleted)))
        .scalars()
        .one()
    )
    assert row.session_id is None


async def test_archived_at_defaults_null_and_settable(db_session):
    memory = MemoryModel(workspace_id=_WS, content="fact", scope={})
    knowledge = KnowledgeMemoryModel(workspace_id=_WS, agent_id=uuid.uuid4(), content="doc")
    db_session.add_all([memory, knowledge])
    await db_session.flush()

    assert memory.archived_at is None
    assert knowledge.archived_at is None
    assert memory.deleted is False

    memory.archived_at = memory.updated_at
    await db_session.flush()
    assert memory.archived_at is not None


async def test_edit_log_lifecycle_reason(db_session):
    """A lifecycle-sourced audit row records its cause in ``reason``."""
    target = uuid.uuid4()
    db_session.add(
        MemoryEditLogModel(
            workspace_id=_WS,
            agent_id=uuid.uuid4(),
            tool_name="lifecycle",
            target_type="user_memory",
            target_id=target,
            reason="ttl_expired",
        )
    )
    await db_session.flush()

    row = (await db_session.execute(select(MemoryEditLogModel).where(~MemoryEditLogModel.deleted))).scalars().one()
    assert row.tool_name == "lifecycle"
    assert row.reason == "ttl_expired"


def test_lifecycle_flags_default_off():
    """Both new switches default off — byte-identical posture."""
    assert settings.MEMORY_FLUSH_ENABLED is False
    assert settings.MEMORY_LIFECYCLE_ENABLED is False


def test_lifecycle_platform_defaults():
    """TTL defaults: L3 episodic bounded, semantic/L4 evergreen; caps sane."""
    assert settings.MEMORY_TTL_L3_EPISODIC_DAYS == 365
    assert settings.MEMORY_TTL_L3_SEMANTIC_DAYS == 0
    assert settings.MEMORY_TTL_L4_DAYS == 0
    assert settings.MEMORY_POLICY_MAX_TTL_DAYS >= settings.MEMORY_TTL_L3_EPISODIC_DAYS
    assert max(settings.MEMORY_CAPACITY_L3, settings.MEMORY_CAPACITY_L4) <= settings.MEMORY_POLICY_MAX_CAPACITY
    assert settings.MEMORY_LIFECYCLE_PROTECTION_WINDOW_DAYS > 0
