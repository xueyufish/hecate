"""Tests for IMSessionRouter.

Validates the deterministic conversation-UUID derivation and the
resolve-or-create flow with workspace scoping.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.channel.gateway.im_session_router import (
    IMSessionRouter,
    derive_conversation_id,
)
from hecate.models.organization import OrganizationModel
from hecate.models.workspace import WorkspaceModel


def test_derive_conversation_id_is_deterministic() -> None:
    """Same (workspace, user) always yields the same UUID."""
    ws = uuid.uuid4()
    user = uuid.uuid4()
    a = derive_conversation_id(ws, user, "feishu", "cli_xxx")
    b = derive_conversation_id(ws, user, "feishu", "cli_xxx")
    assert a == b
    # Different channel yields the same UUID (cross-channel sharing).
    c = derive_conversation_id(ws, user, "slack", "T1")
    assert a == c


def test_derive_conversation_id_is_a_uuid() -> None:
    out = derive_conversation_id(uuid.uuid4(), uuid.uuid4(), "feishu", "cli_xxx")
    assert isinstance(out, uuid.UUID)


def test_derive_conversation_id_handles_missing_app_id() -> None:
    """When im_app_id is None the hash still produces a stable UUID."""
    ws = uuid.uuid4()
    user = uuid.uuid4()
    a = derive_conversation_id(ws, user, "feishu", None)
    b = derive_conversation_id(ws, user, "feishu", None)
    assert a == b


async def _setup_workspace(session: AsyncSession, slug: str) -> WorkspaceModel:
    org = OrganizationModel(
        name=f"Org-{slug}",
        slug=f"org-{slug}",
        owner_id=uuid.uuid4(),
    )
    session.add(org)
    await session.flush()
    ws = WorkspaceModel(org_id=org.id, name=f"WS-{slug}", slug=f"ws-{slug}")
    session.add(ws)
    await session.flush()
    return ws


@pytest.mark.asyncio
async def test_resolve_or_create_creates_conversation(db_session: AsyncSession) -> None:
    ws = await _setup_workspace(db_session, "r1")
    user_id = uuid.uuid4()
    router = IMSessionRouter()
    conversation, session_row = await router.resolve_or_create(
        session=db_session,
        workspace_id=ws.id,
        user_id=user_id,
        channel_type="feishu",
        im_app_id="cli_xxx",
        chat_id="oc_chat",
    )
    assert conversation.workspace_id == ws.id
    assert conversation.source_channel == "feishu"
    assert conversation.im_chat_id == "oc_chat"
    assert session_row.source_channel == "feishu"
    assert session_row.status == "active"


@pytest.mark.asyncio
async def test_resolve_or_create_shares_conversation_across_channels(
    db_session: AsyncSession,
) -> None:
    """The same (workspace, user) routed via two different channels MUST
    resolve to the same conversation UUID."""
    ws = await _setup_workspace(db_session, "r2")
    user_id = uuid.uuid4()
    router = IMSessionRouter()
    conv_feishu, _ = await router.resolve_or_create(
        session=db_session,
        workspace_id=ws.id,
        user_id=user_id,
        channel_type="feishu",
        im_app_id="cli_xxx",
        chat_id="oc_chat",
    )
    conv_slack, _ = await router.resolve_or_create(
        session=db_session,
        workspace_id=ws.id,
        user_id=user_id,
        channel_type="slack",
        im_app_id="T1",
        chat_id="C456",
    )
    assert conv_feishu.id == conv_slack.id


@pytest.mark.asyncio
async def test_resolve_or_create_separates_users(db_session: AsyncSession) -> None:
    """Different users in the same workspace MUST NOT share conversations."""
    ws = await _setup_workspace(db_session, "r3")
    router = IMSessionRouter()
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    conv_a, _ = await router.resolve_or_create(
        session=db_session,
        workspace_id=ws.id,
        user_id=user_a,
        channel_type="feishu",
        im_app_id="cli_xxx",
        chat_id="oc_chat",
    )
    conv_b, _ = await router.resolve_or_create(
        session=db_session,
        workspace_id=ws.id,
        user_id=user_b,
        channel_type="feishu",
        im_app_id="cli_xxx",
        chat_id="oc_chat",
    )
    assert conv_a.id != conv_b.id
