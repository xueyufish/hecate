"""Tests for IMIdentityBindingModel.

Covers unique-key constraints, workspace-scoped lookups, soft delete
semantics, and cross-workspace isolation.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.im_identity_binding import IMIdentityBindingModel
from hecate.models.organization import OrganizationModel
from hecate.models.user import UserModel
from hecate.models.workspace import WorkspaceModel


async def _setup_org_workspace_user(
    session: AsyncSession,
    *,
    suffix: str = "main",
) -> tuple[WorkspaceModel, UserModel]:
    """Create an org / workspace / user triplet for binding tests."""
    org = OrganizationModel(
        name=f"Org-{suffix}",
        slug=f"org-{suffix}",
        owner_id=uuid.uuid4(),
    )
    session.add(org)
    await session.flush()
    ws = WorkspaceModel(
        org_id=org.id,
        name=f"WS-{suffix}",
        slug=f"ws-{suffix}",
    )
    session.add(ws)
    await session.flush()
    user = UserModel(
        email=f"u-{suffix}@example.com",
        hashed_password="bcrypt-test",  # noqa: S106
    )
    session.add(user)
    await session.flush()
    return ws, user


@pytest.mark.asyncio
async def test_create_binding_persists_fields(db_session: AsyncSession) -> None:
    ws, user = await _setup_org_workspace_user(db_session, suffix="create")
    binding = IMIdentityBindingModel(
        workspace_id=ws.id,
        user_id=user.id,
        channel_type="feishu",
        im_app_id="cli_xxx",
        im_user_id="ou_abc",
        metadata_={"display_name": "张三"},
    )
    db_session.add(binding)
    await db_session.flush()
    assert binding.id is not None
    assert binding.channel_type == "feishu"
    assert binding.deleted is False


@pytest.mark.asyncio
async def test_unique_binding_per_workspace_channel_app_user(
    db_session: AsyncSession,
) -> None:
    """A second active binding for the same (workspace, channel, app, im_user)
    tuple MUST be rejected by the service layer."""
    from hecate.channel.im.binding import IMBindingService, TokenError

    ws, user = await _setup_org_workspace_user(db_session, suffix="uniq")
    db_session.add(
        IMIdentityBindingModel(
            workspace_id=ws.id,
            user_id=user.id,
            channel_type="feishu",
            im_app_id="cli_xxx",
            im_user_id="ou_abc",
        )
    )
    await db_session.flush()

    other_user = UserModel(
        email="other@example.com",
        hashed_password="bcrypt-test",  # noqa: S106
    )
    db_session.add(other_user)
    await db_session.flush()
    # Issue a token for the conflicting identity and try to confirm it.
    service = IMBindingService()
    token = await service.issue_token(
        workspace_id=ws.id,
        channel_type="feishu",
        im_app_id="cli_xxx",
        im_user_id="ou_abc",
        _session=db_session,
    )
    with pytest.raises(TokenError):
        await service.confirm_token(token, bound_user_id=other_user.id, _session=db_session)


@pytest.mark.asyncio
async def test_one_user_can_hold_multiple_bindings(db_session: AsyncSession) -> None:
    ws, user = await _setup_org_workspace_user(db_session, suffix="multi")
    feishu = IMIdentityBindingModel(
        workspace_id=ws.id,
        user_id=user.id,
        channel_type="feishu",
        im_app_id="cli_xxx",
        im_user_id="ou_abc",
    )
    slack = IMIdentityBindingModel(
        workspace_id=ws.id,
        user_id=user.id,
        channel_type="slack",
        im_app_id="T123",
        im_user_id="U456",
    )
    db_session.add_all([feishu, slack])
    await db_session.flush()
    assert feishu.id != slack.id


@pytest.mark.asyncio
async def test_cross_workspace_isolation(db_session: AsyncSession) -> None:
    """Same IM identity may bind to different users across workspaces."""
    ws_a, user_a = await _setup_org_workspace_user(db_session, suffix="ws-a")
    ws_b, user_b = await _setup_org_workspace_user(db_session, suffix="ws-b")
    bind_a = IMIdentityBindingModel(
        workspace_id=ws_a.id,
        user_id=user_a.id,
        channel_type="feishu",
        im_app_id="cli_xxx",
        im_user_id="ou_abc",
    )
    bind_b = IMIdentityBindingModel(
        workspace_id=ws_b.id,
        user_id=user_b.id,
        channel_type="feishu",
        im_app_id="cli_xxx",
        im_user_id="ou_abc",
    )
    db_session.add_all([bind_a, bind_b])
    await db_session.flush()
    assert bind_a.workspace_id != bind_b.workspace_id


@pytest.mark.asyncio
async def test_soft_delete(db_session: AsyncSession) -> None:
    """Soft-deleting a binding frees up the unique key for re-binding."""
    ws, user = await _setup_org_workspace_user(db_session, suffix="soft")
    binding = IMIdentityBindingModel(
        workspace_id=ws.id,
        user_id=user.id,
        channel_type="feishu",
        im_app_id="cli_xxx",
        im_user_id="ou_abc",
    )
    db_session.add(binding)
    await db_session.flush()
    binding.deleted = True
    binding.deleted_at = __import__("datetime").datetime.now(__import__("datetime").UTC)
    await db_session.flush()
    other_user = UserModel(
        email="other2@example.com",
        hashed_password="bcrypt-test",  # noqa: S106
    )
    db_session.add(other_user)
    await db_session.flush()
    new_binding = IMIdentityBindingModel(
        workspace_id=ws.id,
        user_id=other_user.id,
        channel_type="feishu",
        im_app_id="cli_xxx",
        im_user_id="ou_abc",
    )
    db_session.add(new_binding)
    await db_session.flush()
    assert new_binding.id != binding.id
