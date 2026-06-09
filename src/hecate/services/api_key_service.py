"""API key service for database-backed key lifecycle management.

Handles generation, verification, rotation, and revocation of API keys
with system or workspace scoping. Keys use the ``hcat_`` prefix format
and are stored as SHA-256 hashes.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.api_key import ApiKeyModel, ApiKeyScope

logger = logging.getLogger(__name__)

_KEY_PREFIX = "hcat_"
_KEY_BYTES = 32


def _generate_key() -> tuple[str, str, str]:
    """Generate a new API key, returning (raw_key, sha256_hash, prefix)."""
    raw = secrets.token_urlsafe(_KEY_BYTES)
    full_key = f"{_KEY_PREFIX}{raw}"
    key_hash = hashlib.sha256(full_key.encode()).hexdigest()
    prefix = full_key[:8]
    return full_key, key_hash, prefix


class ApiKeyService:
    """Manages API key lifecycle — create, verify, rotate, revoke."""

    async def create_key(
        self,
        db: AsyncSession,
        *,
        name: str,
        scope: ApiKeyScope,
        created_by: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        org_id: uuid.UUID | None = None,
        expires_at: datetime | None = None,
    ) -> tuple[ApiKeyModel, str]:
        """Create a new API key.

        Args:
            db: Async database session.
            name: Human-readable key label.
            scope: System or workspace scope.
            created_by: User ID of the key creator.
            workspace_id: Required for workspace scope.
            org_id: Required for workspace scope.
            expires_at: Optional expiration timestamp.

        Returns:
            Tuple of (ApiKeyModel, raw_key). The raw key is shown only once.

        Raises:
            ValueError: If workspace scope is missing workspace_id.
        """
        if scope == ApiKeyScope.WORKSPACE and workspace_id is None:
            raise ValueError("workspace_id is required for workspace-scoped keys")

        raw_key, key_hash, key_prefix = _generate_key()

        api_key = ApiKeyModel(
            name=name,
            key_hash=key_hash,
            key_prefix=key_prefix,
            scope=scope,
            org_id=org_id,
            workspace_id=workspace_id,
            created_by=created_by,
            expires_at=expires_at,
            is_active=True,
        )
        db.add(api_key)
        await db.flush()
        logger.info("API key created: %s (scope=%s)", name, scope.value)
        return api_key, raw_key

    async def verify_key(self, db: AsyncSession, raw_key: str) -> ApiKeyModel | None:
        """Verify a raw API key by hash lookup.

        Updates ``last_used_at`` on successful verification.

        Args:
            db: Async database session.
            raw_key: The full API key string to verify.

        Returns:
            The ApiKeyModel if valid, None otherwise.
        """
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        result = await db.execute(
            select(ApiKeyModel).where(
                ApiKeyModel.key_hash == key_hash,
                ApiKeyModel.is_active.is_(True),
                ApiKeyModel.deleted.is_(False),
            )
        )
        api_key = result.scalar_one_or_none()
        if api_key is None:
            return None

        # Check expiration
        if api_key.expires_at is not None and api_key.expires_at < datetime.now(UTC):
            return None

        api_key.last_used_at = datetime.now(UTC)
        await db.flush()
        return api_key

    async def rotate_key(self, db: AsyncSession, key_id: uuid.UUID) -> tuple[ApiKeyModel, str]:
        """Rotate an API key — creates replacement, revokes old.

        Args:
            db: Async database session.
            key_id: The ID of the key to rotate.

        Returns:
            Tuple of (new ApiKeyModel, new raw key).

        Raises:
            ValueError: If the key is not found or already inactive.
        """
        result = await db.execute(
            select(ApiKeyModel).where(
                ApiKeyModel.id == key_id,
                ApiKeyModel.deleted.is_(False),
            )
        )
        old_key = result.scalar_one_or_none()
        if old_key is None:
            raise ValueError("API key not found")
        if not old_key.is_active:
            raise ValueError("API key is already inactive")

        # Generate new key
        raw_key, key_hash, key_prefix = _generate_key()

        new_key = ApiKeyModel(
            name=old_key.name,
            key_hash=key_hash,
            key_prefix=key_prefix,
            scope=old_key.scope,
            org_id=old_key.org_id,
            workspace_id=old_key.workspace_id,
            created_by=old_key.created_by,
            expires_at=old_key.expires_at,
            is_active=True,
        )
        db.add(new_key)

        # Revoke old key
        old_key.is_active = False
        await db.flush()

        logger.info("API key rotated: %s → new key", old_key.name)
        return new_key, raw_key

    async def revoke_key(self, db: AsyncSession, key_id: uuid.UUID) -> None:
        """Revoke (deactivate) an API key.

        Args:
            db: Async database session.
            key_id: The ID of the key to revoke.

        Raises:
            ValueError: If the key is not found.
        """
        result = await db.execute(
            select(ApiKeyModel).where(
                ApiKeyModel.id == key_id,
                ApiKeyModel.deleted.is_(False),
            )
        )
        api_key = result.scalar_one_or_none()
        if api_key is None:
            raise ValueError("API key not found")

        api_key.is_active = False
        await db.flush()
        logger.info("API key revoked: %s", api_key.name)

    async def list_keys(
        self,
        db: AsyncSession,
        created_by: uuid.UUID,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[ApiKeyModel], int]:
        """List API keys created by a user.

        Args:
            db: Async database session.
            created_by: Filter by creator user ID.
            page: Page number (1-based).
            page_size: Items per page.

        Returns:
            Tuple of (keys, total_count).
        """
        conditions = [
            ApiKeyModel.created_by == created_by,
            ApiKeyModel.deleted.is_(False),
        ]
        count_stmt = select(func.count()).select_from(ApiKeyModel).where(*conditions)
        total = (await db.execute(count_stmt)).scalar_one()

        offset = (page - 1) * page_size
        stmt = (
            select(ApiKeyModel)
            .where(*conditions)
            .order_by(ApiKeyModel.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        result = await db.execute(stmt)
        keys = list(result.scalars().all())
        return keys, total
