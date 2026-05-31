"""Knowledge base management API endpoints.

Provides operations for knowledge bases:
- ``POST /api/knowledge-bases`` — Create a new knowledge base
- ``GET /api/knowledge-bases`` — List knowledge bases (paginated)
- ``POST /api/knowledge-bases/{id}/documents`` — Upload a document
- ``GET /api/knowledge-bases/{id}/documents`` — List documents in knowledge base
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel as PydanticBase
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.deps import get_db, verify_api_key
from hecate.models.document import DocumentModel, DocumentReadSchema
from hecate.models.knowledge import (
    KnowledgeBaseCreateSchema,
    KnowledgeBaseModel,
    KnowledgeBaseReadSchema,
)
from hecate.services.rag.service import knowledge_base_service

router = APIRouter()


@router.post("/knowledge-bases", status_code=status.HTTP_201_CREATED)
async def create_knowledge_base(
    data: KnowledgeBaseCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    api_key: Annotated[str, Depends(verify_api_key)],
) -> dict:
    """Create a new knowledge base.

    Args:
        data: The knowledge base creation data.
        db: The async database session.
        api_key: The validated API key.

    Returns:
        dict: The created knowledge base data.
    """
    collection_name = f"kb_{uuid.uuid4().hex[:12]}"
    kb = KnowledgeBaseModel(
        name=data.name,
        description=data.description,
        embedding_model=data.embedding_model,
        chunk_strategy=data.chunk_strategy,
        chunk_size=data.chunk_size,
        chunk_overlap=data.chunk_overlap,
        qdrant_collection=collection_name,
    )
    db.add(kb)
    await db.flush()
    await db.refresh(kb)
    return KnowledgeBaseReadSchema.model_validate(kb).model_dump()


@router.get("/knowledge-bases")
async def list_knowledge_bases(
    db: Annotated[AsyncSession, Depends(get_db)],
    api_key: Annotated[str, Depends(verify_api_key)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """List knowledge bases with pagination.

    Args:
        db: The async database session.
        api_key: The validated API key.
        page: Page number (1-indexed).
        page_size: Number of items per page.

    Returns:
        dict: ``{"items": [...], "total": int}`` with knowledge base list and total count.
    """
    count_stmt = select(func.count()).select_from(KnowledgeBaseModel).where(KnowledgeBaseModel.deleted_at.is_(None))
    total = (await db.execute(count_stmt)).scalar_one()

    offset = (page - 1) * page_size
    stmt = (
        select(KnowledgeBaseModel)
        .where(KnowledgeBaseModel.deleted_at.is_(None))
        .order_by(KnowledgeBaseModel.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    kbs = result.scalars().all()

    return {
        "items": [KnowledgeBaseReadSchema.model_validate(kb).model_dump() for kb in kbs],
        "total": total,
    }


@router.post("/knowledge-bases/{kb_id}/documents", status_code=status.HTTP_201_CREATED)
async def upload_document(
    kb_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    api_key: Annotated[str, Depends(verify_api_key)],
    filename: str = "untitled",
    file_path: str = "",
    file_size: int = 0,
    content_type: str | None = None,
) -> dict:
    """Upload a document to a knowledge base.

    Args:
        kb_id: The UUID of the knowledge base.
        db: The async database session.
        api_key: The validated API key.
        filename: The original filename.
        file_path: The MinIO storage path.
        file_size: The file size in bytes.
        content_type: The MIME content type.

    Returns:
        dict: The created document record.

    Raises:
        HTTPException: 404 if knowledge base not found.
    """
    result = await db.execute(
        select(KnowledgeBaseModel).where(
            KnowledgeBaseModel.id == kb_id,
            KnowledgeBaseModel.deleted_at.is_(None),
        )
    )
    kb = result.scalar_one_or_none()
    if kb is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "Knowledge base not found", "details": None}},
        )

    doc = DocumentModel(
        knowledge_base_id=kb_id,
        filename=filename,
        file_path=file_path,
        file_size=file_size,
        content_type=content_type,
        parsing_status="pending",
    )
    db.add(doc)
    await db.flush()
    await db.refresh(doc)
    return DocumentReadSchema.model_validate(doc).model_dump()


@router.get("/knowledge-bases/{kb_id}/documents")
async def list_documents(
    kb_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    api_key: Annotated[str, Depends(verify_api_key)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """List documents in a knowledge base.

    Args:
        kb_id: The UUID of the knowledge base.
        db: The async database session.
        api_key: The validated API key.
        page: Page number (1-indexed).
        page_size: Number of items per page.

    Returns:
        dict: ``{"items": [...], "total": int}`` with document list and total count.
    """
    base_query = select(DocumentModel).where(
        DocumentModel.knowledge_base_id == kb_id,
        DocumentModel.deleted_at.is_(None),
    )

    count_stmt = select(func.count()).select_from(base_query.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    offset = (page - 1) * page_size
    stmt = base_query.order_by(DocumentModel.created_at.desc()).offset(offset).limit(page_size)
    result = await db.execute(stmt)
    docs = result.scalars().all()

    return {
        "items": [DocumentReadSchema.model_validate(d).model_dump() for d in docs],
        "total": total,
    }


async def _get_kb_or_404(kb_id: uuid.UUID, db: AsyncSession) -> KnowledgeBaseModel:
    """Look up a knowledge base by ID or raise 404."""
    result = await db.execute(
        select(KnowledgeBaseModel).where(
            KnowledgeBaseModel.id == kb_id,
            KnowledgeBaseModel.deleted_at.is_(None),
        )
    )
    kb = result.scalar_one_or_none()
    if kb is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "Knowledge base not found", "details": None}},
        )
    return kb


class KBSearchRequest(PydanticBase):
    query: str = Field(..., min_length=1)
    mode: str = "hybrid"
    limit: int = 10


class KBCompareRequest(PydanticBase):
    query: str = Field(..., min_length=1)
    limit: int = 5


@router.post("/knowledge-bases/{kb_id}/search")
async def search_knowledge_base(
    kb_id: uuid.UUID,
    data: KBSearchRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    api_key: Annotated[str, Depends(verify_api_key)],
) -> dict[str, Any]:
    """Search a knowledge base for hit testing.

    Args:
        kb_id: The UUID of the knowledge base.
        data: Search request with query, mode, and limit.
        db: The async database session.
        api_key: The validated API key.

    Returns:
        dict: Search results with score breakdown.
    """
    kb = await _get_kb_or_404(kb_id, db)
    mode = data.mode if data.mode in ("hybrid", "dense", "sparse") else "hybrid"

    results = await knowledge_base_service.search_with_score_breakdown(
        collection_name=kb.qdrant_collection,
        query=data.query,
        limit=data.limit,
        mode=mode,
    )

    return {
        "query": data.query,
        "mode": mode,
        "total": len(results),
        "results": [
            {
                "id": r.id,
                "score": r.score,
                "content": r.content,
                "metadata": r.metadata,
                "dense_score": r.dense_score,
                "sparse_score": r.sparse_score,
            }
            for r in results
        ],
    }


@router.get("/knowledge-bases/{kb_id}/chunks")
async def list_knowledge_base_chunks(
    kb_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    api_key: Annotated[str, Depends(verify_api_key)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
) -> dict:
    """List stored chunks in a knowledge base.

    Args:
        kb_id: The UUID of the knowledge base.
        db: The async database session.
        api_key: The validated API key.
        page: Page number (1-indexed).
        page_size: Number of items per page.

    Returns:
        dict: ``{"items": [...], "total": int}`` with chunk list and total count.
    """
    kb = await _get_kb_or_404(kb_id, db)
    return await knowledge_base_service.list_chunks(
        collection_name=kb.qdrant_collection,
        page=page,
        page_size=page_size,
    )


@router.post("/knowledge-bases/{kb_id}/compare")
async def compare_search_modes(
    kb_id: uuid.UUID,
    data: KBCompareRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    api_key: Annotated[str, Depends(verify_api_key)],
) -> dict[str, Any]:
    """Compare search modes for a query.

    Runs the same query across dense, sparse, and hybrid modes
    and returns side-by-side results.

    Args:
        kb_id: The UUID of the knowledge base.
        data: Compare request with query and limit.
        db: The async database session.
        api_key: The validated API key.

    Returns:
        dict: Results per mode (dense, sparse, hybrid).
    """
    kb = await _get_kb_or_404(kb_id, db)
    return await knowledge_base_service.compare_modes(
        collection_name=kb.qdrant_collection,
        query=data.query,
        limit=data.limit,
    )
