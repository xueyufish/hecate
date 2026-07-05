"""A2A management API endpoints."""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel as PydanticBase
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.database import get_db
from hecate.models.agent_card_key import AgentCardKeyModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/a2a", tags=["a2a-management"])


class KeyGenerationResponse(PydanticBase):
    """Response for key generation."""

    kid: str
    algorithm: str
    status: str


class KeyListResponse(PydanticBase):
    """Response for listing keys."""

    keys: list[dict]


@router.post("/keys/generate", response_model=KeyGenerationResponse)
async def generate_signing_key(
    db: Annotated[AsyncSession, Depends(get_db)],
    workspace_id: UUID | None = None,
) -> KeyGenerationResponse:
    """Generate a new signing key pair for AgentCard signing.

    Args:
        db: Database session.
        workspace_id: Optional workspace ID (defaults to zero UUID).

    Returns:
        Generated key information.
    """
    import uuid

    from hecate.a2a.signing import generate_es256_keypair

    private_jwk, public_jwk = generate_es256_keypair()
    kid = str(uuid.uuid4())

    key = AgentCardKeyModel(
        kid=kid,
        private_key=private_jwk,
        public_key=public_jwk,
        algorithm="ES256",
        workspace_id=workspace_id or uuid.UUID("00000000-0000-0000-0000-000000000000"),
        status="active",
    )
    db.add(key)
    await db.flush()

    return KeyGenerationResponse(
        kid=kid,
        algorithm="ES256",
        status="active",
    )


@router.get("/keys", response_model=KeyListResponse)
async def list_signing_keys(
    db: Annotated[AsyncSession, Depends(get_db)],
    workspace_id: UUID | None = None,
) -> KeyListResponse:
    """List all signing keys for a workspace.

    Args:
        db: Database session.
        workspace_id: Optional workspace ID filter.

    Returns:
        List of signing keys (public parts only).
    """

    stmt = select(AgentCardKeyModel).where(AgentCardKeyModel.deleted.is_(False))
    if workspace_id:
        stmt = stmt.where(AgentCardKeyModel.workspace_id == workspace_id)

    result = await db.execute(stmt)
    keys = result.scalars().all()

    return KeyListResponse(
        keys=[
            {
                "kid": k.kid,
                "algorithm": k.algorithm,
                "status": k.status,
                "public_key": k.public_key,
            }
            for k in keys
        ]
    )


@router.post("/keys/{kid}/rotate")
async def rotate_signing_key(
    kid: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Rotate a signing key (mark as rotating, generate new active key).

    Args:
        kid: The key ID to rotate.
        db: Database session.

    Returns:
        New key information.
    """
    import uuid

    from hecate.a2a.signing import generate_es256_keypair

    result = await db.execute(
        select(AgentCardKeyModel).where(
            AgentCardKeyModel.kid == kid,
            AgentCardKeyModel.deleted.is_(False),
        )
    )
    old_key = result.scalar_one_or_none()

    if old_key is None:
        raise HTTPException(status_code=404, detail=f"Key {kid} not found")

    # Mark old key as rotating
    old_key.status = "rotating"

    # Generate new key
    private_jwk, public_jwk = generate_es256_keypair()
    new_kid = str(uuid.uuid4())

    new_key = AgentCardKeyModel(
        kid=new_kid,
        private_key=private_jwk,
        public_key=public_jwk,
        algorithm="ES256",
        workspace_id=old_key.workspace_id,
        status="active",
    )
    db.add(new_key)
    await db.flush()

    return {
        "old_kid": kid,
        "old_status": "rotating",
        "new_kid": new_kid,
        "new_status": "active",
    }
