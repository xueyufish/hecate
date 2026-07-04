"""Tests for CatalogService — aggregation, filtering, search, comparison."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from hecate.model_hub.catalog_service import CatalogService


class TestCatalogService:
    async def test_list_models_empty(self, db_session: AsyncSession) -> None:
        service = CatalogService(db_session)
        result = await service.list_models()
        assert result["items"] == []
        assert result["total"] == 0

    async def test_search_models_empty(self, db_session: AsyncSession) -> None:
        service = CatalogService(db_session)
        result = await service.search_models(["vision"])
        assert result == []

    async def test_compare_models_empty(self, db_session: AsyncSession) -> None:
        service = CatalogService(db_session)
        result = await service.compare_models(["nonexistent"])
        assert result == []

    async def test_get_model_not_found(self, db_session: AsyncSession) -> None:
        service = CatalogService(db_session)
        result = await service.get_model("nonexistent")
        assert result is None
