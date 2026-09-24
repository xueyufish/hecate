"""Tests for memory policy resolution (memory-policy capability).

Covers the platform→workspace→agent chain, narrowing-only permission
fields, numeric hard-cap clamping, validation rejections, audit rows and
cache invalidation.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from hecate.core.composition.memory_policy import (
    PolicyValidationError,
    delete_policy,
    invalidate_memory_policy_cache,
    narrowed_tool_names,
    resolve_policy,
    upsert_policy,
)
from hecate.core.config import settings
from hecate.models.memory import MemoryEditLogModel, MemoryPolicyModel

_ZERO = uuid.UUID("00000000-0000-0000-0000-000000000000")
_WS = uuid.uuid4()
_AGENT = uuid.uuid4()


async def _rows(db_session) -> list[MemoryPolicyModel]:
    return (await db_session.execute(select(MemoryPolicyModel).where(~MemoryPolicyModel.deleted))).scalars().all()


async def test_empty_table_resolves_platform_defaults(db_session):
    resolved = await resolve_policy(db_session, _WS, _AGENT)
    assert resolved.tool_subset is None
    assert resolved.sharing_ceiling == "workspace"
    assert resolved.ttl_days["l3_episodic"] == settings.MEMORY_TTL_L3_EPISODIC_DAYS
    assert resolved.ttl_days["l4"] == settings.MEMORY_TTL_L4_DAYS
    assert resolved.flush_enabled is False  # platform flag off is the bound
    assert resolved.promotion_enabled is False


async def test_workspace_then_agent_chain(db_session):
    await upsert_policy(
        db_session,
        _WS,
        None,
        params={"ttl": {"l3_episodic_days": 30}, "promotion": {"enabled": True}},
    )
    invalidate_memory_policy_cache()
    resolved_ws = await resolve_policy(db_session, _WS, _AGENT)
    assert resolved_ws.ttl_days["l3_episodic"] == 30
    assert resolved_ws.promotion_enabled is True

    await upsert_policy(
        db_session,
        _WS,
        _AGENT,
        params={"ttl": {"l3_episodic_days": 7}},
    )
    invalidate_memory_policy_cache()
    resolved_agent = await resolve_policy(db_session, _WS, _AGENT)
    # Agent override narrows the TTL; workspace promotion setting carries.
    assert resolved_agent.ttl_days["l3_episodic"] == 7
    assert resolved_agent.promotion_enabled is True
    # Another agent in the same workspace keeps the workspace value.
    other = await resolve_policy(db_session, _WS, uuid.uuid4())
    assert other.ttl_days["l3_episodic"] == 30


async def test_tool_subset_narrows_only(db_session):
    tools = ["memory_search", "memory_add", "memory_forget", "web_search"]
    await upsert_policy(db_session, _WS, None, tool_subset=["memory_search", "memory_add"])
    invalidate_memory_policy_cache()
    narrowed = await narrowed_tool_names(db_session, _WS, _AGENT, list(tools))
    assert narrowed == ["memory_search", "memory_add", "web_search"]


async def test_agent_tool_subset_intersected_with_workspace(db_session):
    await upsert_policy(
        db_session,
        _WS,
        None,
        tool_subset=["memory_search", "memory_add", "memory_update"],
    )
    # Writing a widening agent subset must be rejected.
    with pytest.raises(PolicyValidationError):
        await upsert_policy(
            db_session,
            _WS,
            _AGENT,
            tool_subset=["memory_search", "memory_forget"],
        )
    # A narrowing subset is accepted and intersected at resolve time.
    await upsert_policy(db_session, _WS, _AGENT, tool_subset=["memory_search", "memory_update"])
    invalidate_memory_policy_cache()
    narrowed = await narrowed_tool_names(db_session, _WS, _AGENT, ["memory_search", "memory_add", "memory_update"])
    assert narrowed == ["memory_search", "memory_update"]


async def test_sharing_ceiling_widen_rejected(db_session):
    await upsert_policy(db_session, _WS, None, sharing_ceiling="team")
    with pytest.raises(PolicyValidationError):
        await upsert_policy(db_session, _WS, _AGENT, sharing_ceiling="workspace")
    await upsert_policy(db_session, _WS, _AGENT, sharing_ceiling="actor")
    invalidate_memory_policy_cache()
    resolved = await resolve_policy(db_session, _WS, _AGENT)
    assert resolved.sharing_ceiling == "actor"


async def test_numeric_cap_boundary_accepted(db_session):
    """A value exactly at the hard cap is accepted; over-cap is rejected."""
    cap = settings.MEMORY_POLICY_MAX_TTL_DAYS
    await upsert_policy(db_session, _WS, None, params={"ttl": {"l3_episodic_days": cap}})
    invalidate_memory_policy_cache()
    resolved = await resolve_policy(db_session, _WS, _AGENT)
    assert resolved.ttl_days["l3_episodic"] == cap
    with pytest.raises(PolicyValidationError):
        await upsert_policy(db_session, _WS, None, params={"ttl": {"l3_episodic_days": cap + 1}})


async def test_validation_rejects_unknown_tool_and_bad_params(db_session):
    with pytest.raises(PolicyValidationError, match="unknown memory tool"):
        await upsert_policy(db_session, _WS, None, tool_subset=["memory_transmogrify"])
    with pytest.raises(PolicyValidationError, match="exceeds platform hard cap"):
        await upsert_policy(
            db_session,
            _WS,
            None,
            params={"ttl": {"l3_episodic_days": 10**9}},
        )
    with pytest.raises(PolicyValidationError, match="unknown policy params groups"):
        await upsert_policy(db_session, _WS, None, params={"wat": {"x": 1}})


async def test_flush_policy_cannot_exceed_platform_flag(db_session):
    """With MEMORY_FLUSH_ENABLED=false (default), no policy can turn flush on."""
    await upsert_policy(db_session, _WS, None, params={"flush": {"enabled": True}})
    invalidate_memory_policy_cache()
    resolved = await resolve_policy(db_session, _WS, _AGENT)
    assert resolved.flush_enabled is False


async def test_policy_write_writes_audit_and_cache_invalidates(db_session):
    await upsert_policy(db_session, _WS, None, params={"ttl": {"l3_episodic_days": 30}})
    # Warm the cache.
    first = await resolve_policy(db_session, _WS, _AGENT)
    assert first.ttl_days["l3_episodic"] == 30
    # Bypass the service to mutate the row, then upsert again: the upsert's
    # invalidation must make the next resolve see the new value.
    await upsert_policy(db_session, _WS, None, params={"ttl": {"l3_episodic_days": 14}})
    second = await resolve_policy(db_session, _WS, _AGENT)
    assert second.ttl_days["l3_episodic"] == 14

    audits = (
        (await db_session.execute(select(MemoryEditLogModel).where(MemoryEditLogModel.target_type == "memory_policy")))
        .scalars()
        .all()
    )
    assert len(audits) == 2
    assert audits[0].tool_name == "policy_upsert"


async def test_delete_policy_falls_back_to_platform(db_session):
    await upsert_policy(db_session, _WS, None, params={"ttl": {"l3_episodic_days": 30}})
    invalidate_memory_policy_cache()
    assert len(await _rows(db_session)) == 1

    assert await delete_policy(db_session, _WS, None) is True
    invalidate_memory_policy_cache()
    assert len(await _rows(db_session)) == 0
    resolved = await resolve_policy(db_session, _WS, _AGENT)
    assert resolved.ttl_days["l3_episodic"] == settings.MEMORY_TTL_L3_EPISODIC_DAYS
    # Deleting again reports False (nothing to delete).
    assert await delete_policy(db_session, _WS, None) is False


async def test_disabled_row_treated_as_absent(db_session):
    await upsert_policy(db_session, _WS, None, params={"ttl": {"l3_episodic_days": 30}})
    invalidate_memory_policy_cache()
    # Disable directly (the API uses enabled=False on upsert).
    await upsert_policy(db_session, _WS, None, enabled=False, params={"ttl": {"l3_episodic_days": 30}})
    invalidate_memory_policy_cache()
    resolved = await resolve_policy(db_session, _WS, _AGENT)
    assert resolved.ttl_days["l3_episodic"] == settings.MEMORY_TTL_L3_EPISODIC_DAYS
