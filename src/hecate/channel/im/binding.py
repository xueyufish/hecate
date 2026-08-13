"""IMBindingService — token issuance, confirmation, and identity resolution.

The service enforces the mandatory Bound Identity model (design.md D3):

- Unbound IM users are issued a one-time, 10-minute token; the plaintext
  token is returned to the caller (to embed in the binding URL) but only
  the SHA-256 hash is persisted.
- Confirmation consumes a token, validates expiry and single-use, and
  creates the corresponding :class:`IMIdentityBindingModel` row in the same
  transaction.
- :meth:`resolve_identity` performs the workspace-scoped lookup used by the
  Gateway to map an inbound IM user to a Hecate user.

Usage::

    service = IMBindingService(session_factory=async_session)
    token = await service.issue_token(workspace_id, "feishu", "cli_xxx", "ou_abc")
    # -> plaintext token, e.g. "abc123..." (return to IM user as URL)

    binding = await service.confirm_token(token, current_user_id)
    # -> IMIdentityBindingModel or raises TokenExpiredError / TokenUsedError

    user = await service.resolve_identity(
        workspace_id, "feishu", "cli_xxx", "ou_abc"
    )
    # -> UserModel or None
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.im_binding_token import IMBindingTokenModel
from hecate.models.im_identity_binding import IMIdentityBindingModel
from hecate.models.user import UserModel

logger = logging.getLogger(__name__)


# Default token validity window — design.md D3 (10 minutes, single-use).
DEFAULT_TOKEN_TTL = timedelta(minutes=10)


class TokenError(Exception):
    """Base class for binding-token errors."""


class TokenExpiredError(TokenError):
    """Raised when a binding token is past its ``expires_at``."""


class TokenUsedError(TokenError):
    """Raised when a binding token has already been confirmed."""


class TokenNotFoundError(TokenError):
    """Raised when a token hash has no matching row."""


def _hash_token(token: str) -> str:
    """Return the SHA-256 hex digest of ``token`` for storage."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_token() -> str:
    """Return a fresh 32-byte URL-safe random token.

    Exposed for testing and for callers that want to mint tokens outside
    the service (e.g., admin scripts).
    """
    return secrets.token_urlsafe(32)


class IMBindingService:
    """Coordinates token issuance, confirmation, and identity resolution."""

    def __init__(
        self,
        session_factory: Callable[..., Any] | None = None,
        token_ttl: timedelta = DEFAULT_TOKEN_TTL,
    ) -> None:
        """Args:
        session_factory: an async context manager that yields an
            ``AsyncSession`` (e.g., ``async_sessionmaker(engine, expire_on_commit=False)``).
            If ``None``, callers must use the ``_session`` argument to
            :meth:`issue_token` / :meth:`confirm_token` / :meth:`resolve_identity`
            explicitly — useful for unit tests.
        token_ttl: token validity window. Defaults to 10 minutes.
        """
        self._session_factory = session_factory
        self._token_ttl = token_ttl

    async def issue_token(
        self,
        workspace_id: Any,
        channel_type: str,
        im_app_id: str,
        im_user_id: str,
        *,
        _session: AsyncSession | None = None,
    ) -> str:
        """Issue a fresh binding token.

        Returns the plaintext token (suitable for embedding in a URL).
        The plaintext is never persisted — only its SHA-256 hash.

        Raises ``RuntimeError`` if no session is available.
        """
        plaintext = generate_token()
        token_hash = _hash_token(plaintext)
        now = datetime.now(UTC)
        expires_at = now + self._token_ttl
        session = _session
        if session is None:
            if self._session_factory is None:
                raise RuntimeError("IMBindingService.issue_token requires either _session or session_factory")
            session = self._session_factory()
        token_row = IMBindingTokenModel(
            workspace_id=workspace_id,
            channel_type=channel_type,
            im_app_id=im_app_id,
            im_user_id=im_user_id,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        session.add(token_row)
        await session.flush()
        logger.info(
            "Issued IM binding token workspace=%s channel=%s app=%s user=%s",
            workspace_id,
            channel_type,
            im_app_id,
            im_user_id,
        )
        return plaintext

    async def confirm_token(
        self,
        plaintext_token: str,
        bound_user_id: Any,
        *,
        _session: AsyncSession | None = None,
    ) -> IMIdentityBindingModel:
        """Validate and consume a binding token, creating the binding.

        On success, returns the newly created :class:`IMIdentityBindingModel`.
        The token row is marked ``confirmed_at = now()`` so subsequent
        attempts fail with :class:`TokenUsedError`.

        Raises:
            TokenNotFoundError: if no row matches the token hash.
            TokenExpiredError: if the token is past ``expires_at``.
            TokenUsedError: if the token was already confirmed.
        """
        if plaintext_token is None or plaintext_token == "":
            raise TokenNotFoundError("Empty token")
        token_hash = _hash_token(plaintext_token)
        session = _session
        if session is None:
            if self._session_factory is None:
                raise RuntimeError("IMBindingService.confirm_token requires either _session or session_factory")
            session = self._session_factory()
        stmt = select(IMBindingTokenModel).where(
            IMBindingTokenModel.token_hash == token_hash,
            IMBindingTokenModel.deleted == False,  # noqa: E712
        )
        result = await session.execute(stmt)
        token_row = result.scalar_one_or_none()
        if token_row is None:
            raise TokenNotFoundError("Token not recognized")
        now = datetime.now(UTC)
        if token_row.confirmed_at is not None:
            raise TokenUsedError("Token already used")
        # SQLite returns naive datetimes when the column type is timezone-aware;
        # normalize before comparison to keep cross-backend behavior consistent.
        expires_at = token_row.expires_at
        if expires_at is not None:
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at < now:
                raise TokenExpiredError("Token expired")
        token_row.confirmed_at = now
        token_row.bound_user_id = bound_user_id
        # Enforce uniqueness of an active binding at the service layer.
        # SQLite treats NULLs as distinct in UNIQUE indexes, so the
        # application layer is the source of truth here. For Postgres
        # deployments a partial unique index on ``unbound_at IS NULL``
        # would provide defense in depth.
        existing_stmt = select(IMIdentityBindingModel.id).where(
            IMIdentityBindingModel.workspace_id == token_row.workspace_id,
            IMIdentityBindingModel.channel_type == token_row.channel_type,
            IMIdentityBindingModel.im_app_id == token_row.im_app_id,
            IMIdentityBindingModel.im_user_id == token_row.im_user_id,
            IMIdentityBindingModel.deleted == False,  # noqa: E712
            IMIdentityBindingModel.unbound_at.is_(None),
        )
        existing = (await session.execute(existing_stmt)).first()
        if existing is not None:
            raise TokenError("IM identity already bound to another user in this workspace")
        binding = IMIdentityBindingModel(
            workspace_id=token_row.workspace_id,
            user_id=bound_user_id,
            channel_type=token_row.channel_type,
            im_app_id=token_row.im_app_id,
            im_user_id=token_row.im_user_id,
        )
        session.add(binding)
        await session.flush()
        logger.info(
            "Confirmed IM binding workspace=%s channel=%s app=%s user=%s bound_user=%s",
            token_row.workspace_id,
            token_row.channel_type,
            token_row.im_app_id,
            token_row.im_user_id,
            bound_user_id,
        )
        return binding

    async def resolve_identity(
        self,
        workspace_id: Any,
        channel_type: str,
        im_app_id: str,
        im_user_id: str,
        *,
        _session: AsyncSession | None = None,
    ) -> UserModel | None:
        """Return the Hecate user bound to the given IM identity, or ``None``.

        Lookup is workspace-scoped to prevent cross-tenant leakage.
        """
        session = _session
        if session is None:
            if self._session_factory is None:
                raise RuntimeError("IMBindingService.resolve_identity requires either _session or session_factory")
            session = self._session_factory()
        stmt = (
            select(IMIdentityBindingModel)
            .where(
                IMIdentityBindingModel.workspace_id == workspace_id,
                IMIdentityBindingModel.channel_type == channel_type,
                IMIdentityBindingModel.im_app_id == im_app_id,
                IMIdentityBindingModel.im_user_id == im_user_id,
                IMIdentityBindingModel.deleted == False,  # noqa: E712
                IMIdentityBindingModel.unbound_at.is_(None),
            )
            .limit(1)
        )
        result = await session.execute(stmt)
        binding = result.scalar_one_or_none()
        if binding is None:
            return None
        user_stmt = select(UserModel).where(
            UserModel.id == binding.user_id,
            UserModel.deleted == False,  # noqa: E712
        )
        user_result = await session.execute(user_stmt)
        return user_result.scalar_one_or_none()
