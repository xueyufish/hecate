"""Unit tests for workspace-level chat engine routing (step5d).

Covers the ``unified-chat-execution`` workspace-override scenarios:
resolution precedence (session affinity > workspace allowlist > global
setting), first-touch session path recording, and the audit-logged
rollout record on override changes.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.config import settings
from hecate.execution.entry_routing import (
    CHAT_ENGINE_ROLLOUT_ACTION,
    SESSION_PATH_METADATA_KEY,
    read_session_path,
    record_rollout_change,
    record_session_path,
    resolve_chat_engine_path,
)
from hecate.models.agent import AgentModel
from hecate.models.audit import AuditLogModel
from hecate.models.feature_flag import FeatureFlagModel
from hecate.models.session import SessionModel

ZERO_WS = uuid.UUID("00000000-0000-0000-0000-000000000000")


@pytest.fixture(autouse=True)
def _restore_global_flag(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "CHAT_TOOL_LOOP_ENGINE_ENABLED", False)
    yield


def _flag(*, status: str = "active", enabled: bool = True, allowlist: list[str] | None = None) -> FeatureFlagModel:
    rules = {"tenant_allowlist": allowlist} if allowlist is not None else None
    return FeatureFlagModel(
        key="chat_tool_loop_engine_enabled",
        status=status,
        enabled=enabled,
        targeting_rules=rules,
    )


async def _session(db_session: AsyncSession, workspace_id: uuid.UUID) -> SessionModel:
    agent = AgentModel(workspace_id=workspace_id, name=f"agent-{uuid.uuid4().hex[:8]}")
    db_session.add(agent)
    await db_session.flush()
    session = SessionModel(agent_id=agent.id, status="active", workspace_id=workspace_id)
    db_session.add(session)
    await db_session.flush()
    return session


# --- resolution precedence ---------------------------------------------------


async def test_no_flag_and_global_off_resolves_direct(db_session) -> None:
    assert await resolve_chat_engine_path(db_session, ZERO_WS) == "direct"


async def test_global_flag_on_resolves_engine(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "CHAT_TOOL_LOOP_ENGINE_ENABLED", True)
    assert await resolve_chat_engine_path(db_session, ZERO_WS) == "engine"


async def test_workspace_allowlist_overrides_global_off(db_session) -> None:
    db_session.add(_flag(allowlist=[str(ZERO_WS)]))
    await db_session.flush()
    assert await resolve_chat_engine_path(db_session, ZERO_WS) == "engine"
    other = uuid.uuid4()
    assert await resolve_chat_engine_path(db_session, other) == "direct"


async def test_workspace_outside_allowlist_blocked_even_with_global_on(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "CHAT_TOOL_LOOP_ENGINE_ENABLED", True)
    db_session.add(_flag(allowlist=[str(uuid.uuid4())]))
    await db_session.flush()
    assert await resolve_chat_engine_path(db_session, ZERO_WS) == "direct"


async def test_flag_without_allowlist_is_global_switch(db_session) -> None:
    db_session.add(_flag(allowlist=None))
    await db_session.flush()
    assert await resolve_chat_engine_path(db_session, ZERO_WS) == "engine"


async def test_draft_flag_falls_through_to_global(db_session) -> None:
    db_session.add(_flag(status="draft", allowlist=[str(ZERO_WS)]))
    await db_session.flush()
    assert await resolve_chat_engine_path(db_session, ZERO_WS) == "direct"


# --- session affinity --------------------------------------------------------


async def test_recorded_session_path_wins_over_workspace_override(db_session) -> None:
    db_session.add(_flag(allowlist=[str(ZERO_WS)]))
    await db_session.flush()
    session = await _session(db_session, ZERO_WS)
    assert await record_session_path(db_session, session.id, "direct")
    assert (
        await resolve_chat_engine_path(
            db_session, ZERO_WS, session_path=await read_session_path(db_session, session.id)
        )
        == "direct"
    )


async def test_record_session_path_is_first_touch_only(db_session) -> None:
    session = await _session(db_session, ZERO_WS)
    assert await record_session_path(db_session, session.id, "engine")
    assert not await record_session_path(db_session, session.id, "direct")
    assert await read_session_path(db_session, session.id) == "engine"


async def test_record_session_path_without_row_is_noop(db_session) -> None:
    assert not await record_session_path(db_session, uuid.uuid4(), "engine")
    assert not await record_session_path(db_session, None, "engine")


# --- rollout record ----------------------------------------------------------


async def test_rollout_change_writes_audit_record(db_session, default_workspace) -> None:
    actor = uuid.uuid4()
    await record_rollout_change(
        db_session,
        workspace_id=default_workspace.id,
        actor_id=actor,
        old_value="direct",
        new_value="engine",
    )
    row = (
        await db_session.execute(select(AuditLogModel).where(AuditLogModel.action == CHAT_ENGINE_ROLLOUT_ACTION))
    ).scalar_one()
    assert row.metadata_["old_value"] == "direct"
    assert row.metadata_["new_value"] == "engine"
    assert str(row.workspace_id) == str(default_workspace.id)


async def test_session_metadata_key_is_namespaced(db_session) -> None:
    session = await _session(db_session, ZERO_WS)
    await record_session_path(db_session, session.id, "engine")
    await db_session.refresh(session)
    assert session.metadata_[SESSION_PATH_METADATA_KEY] == "engine"
