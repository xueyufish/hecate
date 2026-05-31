"""Knowledge base management API endpoints.

Provides operations for knowledge bases:
- ``POST /api/knowledge-bases`` — Create a new knowledge base
- ``GET /api/knowledge-bases`` — List knowledge bases (paginated)
- ``POST /api/knowledge-bases/{id}/documents`` — Upload a document
- ``GET /api/knowledge-bases/{id}/documents`` — List documents in knowledge base
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel as PydanticBase
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.deps import get_db, verify_api_key
from hecate.models.agent import AgentModel, AgentReadSchema
from hecate.models.document import DocumentModel, DocumentReadSchema
from hecate.models.knowledge import (
    KnowledgeBaseCreateSchema,
    KnowledgeBaseModel,
    KnowledgeBaseReadSchema,
)
from hecate.services.rag.crawler import web_crawler
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


async def _cleanup_kb_references(db: AsyncSession, kb_id: uuid.UUID) -> None:
    """Remove a KB ID from all agents' knowledge_base_ids arrays.

    Args:
        db: The async database session.
        kb_id: The UUID of the knowledge base being deleted.
    """
    stmt = select(AgentModel).where(
        AgentModel.deleted_at.is_(None),
    )
    result = await db.execute(stmt)
    agents = result.scalars().all()

    kb_id_str = str(kb_id)
    for agent in agents:
        if isinstance(agent.knowledge_base_ids, list) and kb_id_str in agent.knowledge_base_ids:
            agent.knowledge_base_ids = [kid for kid in agent.knowledge_base_ids if kid != kb_id_str]
            db.add(agent)

    await db.flush()


@router.delete("/knowledge-bases/{kb_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge_base(
    kb_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    api_key: Annotated[str, Depends(verify_api_key)],
) -> None:
    """Soft delete a knowledge base and cascade cleanup agent references.

    Args:
        kb_id: The UUID of the knowledge base to delete.
        db: The async database session.
        api_key: The validated API key.

    Raises:
        HTTPException: 404 if knowledge base not found or already deleted.
    """
    kb = await _get_kb_or_404(kb_id, db)
    kb.deleted_at = datetime.now(UTC)
    await _cleanup_kb_references(db, kb_id)


@router.get("/knowledge-bases/{kb_id}/agents")
async def list_agents_for_knowledge_base(
    kb_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    api_key: Annotated[str, Depends(verify_api_key)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """List agents that reference a specific knowledge base.

    Args:
        kb_id: The UUID of the knowledge base.
        db: The async database session.
        api_key: The validated API key.
        page: Page number (1-indexed).
        page_size: Number of items per page.

    Returns:
        dict: ``{"items": [...], "total": int}`` with agent list and total count.

    Raises:
        HTTPException: 404 if knowledge base not found or deleted.
    """
    await _get_kb_or_404(kb_id, db)

    kb_id_str = str(kb_id)
    stmt = select(AgentModel).where(AgentModel.deleted_at.is_(None))
    result = await db.execute(stmt)
    all_agents = result.scalars().all()

    matching_agents = [
        a for a in all_agents if isinstance(a.knowledge_base_ids, list) and kb_id_str in a.knowledge_base_ids
    ]

    total = len(matching_agents)
    offset = (page - 1) * page_size
    page_agents = matching_agents[offset : offset + page_size]

    return {
        "items": [AgentReadSchema.model_validate(a).model_dump(by_alias=True) for a in page_agents],
        "total": total,
    }


class URLIngestRequest(PydanticBase):
    """Request body for URL ingestion."""

    url: str | None = None
    urls: list[str] | None = None


@router.post("/knowledge-bases/{kb_id}/urls")
async def ingest_urls(
    kb_id: uuid.UUID,
    data: URLIngestRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    api_key: Annotated[str, Depends(verify_api_key)],
) -> dict:
    """Crawl URLs and ingest content into the knowledge base.

    Args:
        kb_id: The UUID of the knowledge base.
        data: Request with url (single) or urls (batch).
        db: The async database session.
        api_key: The validated API key.

    Returns:
        dict: Ingestion results with document_id, chunk_count, metadata.
    """
    kb = await _get_kb_or_404(kb_id, db)

    urls: list[str] = []
    if data.url:
        urls.append(data.url)
    elif data.urls:
        urls.extend(data.urls)
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": {"code": "VALIDATION_ERROR", "message": "Provide url or urls", "details": None}},
        )

    results = await web_crawler.crawl_urls(urls)

    ingested = []
    errors = []
    for result in results:
        if not result.success:
            errors.append({"url": result.url, "error": result.error})
            continue

        virtual_path = f"web://{urlparse(result.url).netloc}{urlparse(result.url).path}"
        doc = DocumentModel(
            knowledge_base_id=kb_id,
            filename=result.title or result.url,
            file_path=virtual_path,
            file_size=len(result.text),
            content_type="text/html",
            parsing_status="completed",
        )
        db.add(doc)
        await db.flush()

        metadata = {
            "source_url": result.url,
            "title": result.title,
            "description": result.description,
        }

        ingest_result = await knowledge_base_service.ingest_document_text(
            text=result.text,
            collection_name=kb.qdrant_collection,
            metadata=metadata,
        )

        ingested.append(
            {
                "document_id": str(doc.id),
                "url": result.url,
                "title": result.title,
                "chunk_count": ingest_result.get("chunk_count", 0),
            }
        )

    return {
        "ingested": ingested,
        "errors": errors,
        "total": len(urls),
        "success": len(ingested),
        "failed": len(errors),
    }
