"""Tests for IMBindingTokenModel.

Covers token issuance (hash-only persistence), expiration, single-use,
and the confirm-token flow that creates the corresponding
IMIdentityBindingModel.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.channel.im.binding import (
    IMBindingService,
    TokenExpiredError,
    TokenNotFoundError,
    TokenUsedError,
    _hash_token,
    generate_token,
)
from hecate.models.im_binding_token import IMBindingTokenModel
from hecate.models.organization import OrganizationModel
from hecate.models.user import UserModel
from hecate.models.workspace import WorkspaceModel


async def _setup(session: AsyncSession) -> tuple[WorkspaceModel, UserModel]:
    org = OrganizationModel(
        name="Org-BT",
        slug="org-bt",
        owner_id=uuid.uuid4(),
    )
    session.add(org)
    await session.flush()
    ws = WorkspaceModel(org_id=org.id, name="WS-BT", slug="ws-bt")
    session.add(ws)
    await session.flush()
    user = UserModel(
        email="bt@example.com",
        hashed_password="bcrypt-test",  # noqa: S106
    )
    session.add(user)
    await session.flush()
    return ws, user


@pytest.mark.asyncio
async def test_generate_token_is_urlsafe_and_unique() -> None:
    """generate_token returns a URL-safe string with sufficient entropy."""
    t1 = generate_token()
    t2 = generate_token()
    assert t1 != t2
    assert len(t1) >= 32  # secrets.token_urlsafe(32) produces ~43 chars


def test_hash_token_is_deterministic_and_sha256() -> None:
    """_hash_token returns the SHA-256 hex digest."""
    token = "abc"
    expected = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert _hash_token(token) == expected


@pytest.mark.asyncio
async def test_issue_token_persists_hash_only(db_session: AsyncSession) -> None:
    """Only the SHA-256 hash is persisted; the plaintext must never appear
    in any stored column."""
    ws, _ = await _setup(db_session)
    service = IMBindingService()
    plaintext = await service.issue_token(
        workspace_id=ws.id,
        channel_type="feishu",
        im_app_id="cli_xxx",
        im_user_id="ou_abc",
        _session=db_session,
    )
    rows = (await db_session.execute(__import__("sqlalchemy").select(IMBindingTokenModel))).scalars().all()
    assert len(rows) == 1
    assert plaintext not in rows[0].token_hash
    expires_at = rows[0].expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    assert expires_at > datetime.now(UTC)
    # Hash matches the SHA-256 of the plaintext
    assert rows[0].token_hash == _hash_token(plaintext)


@pytest.mark.asyncio
async def test_confirm_token_succeeds_and_creates_binding(db_session: AsyncSession) -> None:
    ws, user = await _setup(db_session)
    service = IMBindingService()
    plaintext = await service.issue_token(
        workspace_id=ws.id,
        channel_type="feishu",
        im_app_id="cli_xxx",
        im_user_id="ou_abc",
        _session=db_session,
    )
    binding = await service.confirm_token(
        plaintext,
        bound_user_id=user.id,
        _session=db_session,
    )
    assert binding.workspace_id == ws.id
    assert binding.user_id == user.id
    assert binding.im_user_id == "ou_abc"
    # Token row is marked confirmed
    token_row = (await db_session.execute(__import__("sqlalchemy").select(IMBindingTokenModel))).scalars().one()
    assert token_row.confirmed_at is not None
    assert token_row.bound_user_id == user.id


@pytest.mark.asyncio
async def test_confirm_token_rejects_expired(db_session: AsyncSession) -> None:
    ws, user = await _setup(db_session)
    service = IMBindingService()
    plaintext = await service.issue_token(
        workspace_id=ws.id,
        channel_type="feishu",
        im_app_id="cli_xxx",
        im_user_id="ou_abc",
        _session=db_session,
    )
    # Force expiry into the past
    from sqlalchemy import select as _sel

    token_row = (await db_session.execute(_sel(IMBindingTokenModel))).scalars().one()
    token_row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.flush()
    with pytest.raises(TokenExpiredError):
        await service.confirm_token(plaintext, bound_user_id=user.id, _session=db_session)


@pytest.mark.asyncio
async def test_confirm_token_rejects_second_use(db_session: AsyncSession) -> None:
    ws, user = await _setup(db_session)
    service = IMBindingService()
    plaintext = await service.issue_token(
        workspace_id=ws.id,
        channel_type="feishu",
        im_app_id="cli_xxx",
        im_user_id="ou_abc",
        _session=db_session,
    )
    await service.confirm_token(plaintext, bound_user_id=user.id, _session=db_session)
    with pytest.raises(TokenUsedError):
        await service.confirm_token(plaintext, bound_user_id=user.id, _session=db_session)


@pytest.mark.asyncio
async def test_confirm_token_rejects_unknown(db_session: AsyncSession) -> None:
    ws, user = await _setup(db_session)
    service = IMBindingService()
    with pytest.raises(TokenNotFoundError):
        await service.confirm_token("not-a-real-token", bound_user_id=user.id, _session=db_session)


@pytest.mark.asyncio
async def test_resolve_identity_returns_user_or_none(db_session: AsyncSession) -> None:
    from hecate.models.im_identity_binding import IMIdentityBindingModel

    ws, user = await _setup(db_session)
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
    service = IMBindingService()
    resolved = await service.resolve_identity(
        ws.id,
        "feishu",
        "cli_xxx",
        "ou_abc",
        _session=db_session,
    )
    assert resolved is not None
    assert resolved.id == user.id

    none_result = await service.resolve_identity(
        ws.id,
        "feishu",
        "cli_xxx",
        "ou_xyz_unknown",
        _session=db_session,
    )
    assert none_result is None
