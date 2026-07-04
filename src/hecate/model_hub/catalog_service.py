"""Model catalog service — aggregates registry, provider, and pricing data.

Provides a unified catalog view with search, filter, comparison,
and capability badge computation.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.model_pricing import ModelPricingModel
from hecate.models.model_provider import ModelProviderModel, ModelRegistryModel

logger = logging.getLogger(__name__)

_DEFAULT_WORKSPACE = uuid.UUID("00000000-0000-0000-0000-000000000000")


class CatalogService:
    """Service for model catalog aggregation and search.

    Args:
        db: Async SQLAlchemy session.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_models(
        self,
        provider: str | None = None,
        capability: str | None = None,
        model_type: str | None = None,
        min_context: int | None = None,
        max_input_price: float | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        """List catalog entries with optional filters and pagination.

        Args:
            provider: Filter by provider name.
            capability: Filter by capability tag.
            model_type: Filter by model type (chat, embedding, etc.).
            min_context: Minimum context window.
            max_input_price: Maximum input price per 1K tokens.
            page: Page number (1-indexed).
            page_size: Items per page.

        Returns:
            Dict with items, total, page, page_size.
        """
        conditions = [
            ModelRegistryModel.deleted.is_(False),
            ModelRegistryModel.is_enabled.is_(True),
        ]
        if provider:
            conditions.append(ModelProviderModel.name == provider)
        if model_type:
            conditions.append(ModelRegistryModel.model_type == model_type)
        if min_context is not None:
            conditions.append(ModelRegistryModel.max_context >= min_context)

        stmt = (
            select(ModelRegistryModel, ModelProviderModel)
            .join(ModelProviderModel, ModelRegistryModel.provider_id == ModelProviderModel.id)
            .where(*conditions)
            .order_by(ModelRegistryModel.model_id)
        )

        # Count total
        count_stmt = (
            select(func.count())
            .select_from(ModelRegistryModel)
            .join(ModelProviderModel, ModelRegistryModel.provider_id == ModelProviderModel.id)
            .where(*conditions)
        )
        total = (await self._db.execute(count_stmt)).scalar_one()

        # Paginate
        offset = (page - 1) * page_size
        stmt = stmt.offset(offset).limit(page_size)
        result = await self._db.execute(stmt)
        rows = result.all()

        items = []
        for registry, provider in rows:
            entry = await self._build_catalog_entry(registry, provider)

            # Post-query filters
            if capability and capability not in entry.get("capability_badges", []):
                continue
            if max_input_price is not None:
                pricing = entry.get("effective_pricing")
                if pricing and pricing.get("input_per_1k", 0) > max_input_price:
                    continue

            items.append(entry)

        return {"items": items, "total": total, "page": page, "page_size": page_size}

    async def get_model(self, model_id: str) -> dict[str, Any] | None:
        """Get detailed catalog entry for a single model.

        Args:
            model_id: The model identifier.

        Returns:
            Detailed catalog entry or None if not found.
        """
        stmt = (
            select(ModelRegistryModel, ModelProviderModel)
            .join(ModelProviderModel, ModelRegistryModel.provider_id == ModelProviderModel.id)
            .where(
                ModelRegistryModel.model_id == model_id,
                ModelRegistryModel.deleted.is_(False),
            )
        )
        result = await self._db.execute(stmt)
        row = result.one_or_none()
        if row is None:
            return None

        registry, provider = row
        entry = await self._build_catalog_entry(registry, provider)

        # Add pricing history
        pricing_stmt = (
            select(ModelPricingModel)
            .where(
                ModelPricingModel.model_id == model_id,
                ModelPricingModel.deleted.is_(False),
            )
            .order_by(ModelPricingModel.effective_from.desc())
        )
        pricing_result = await self._db.execute(pricing_stmt)
        pricing_history = []
        for p in pricing_result.scalars().all():
            pricing_history.append(
                {
                    "input_per_1k": p.input_price_per_1k,
                    "output_per_1k": p.output_price_per_1k,
                    "currency": p.currency,
                    "effective_from": p.effective_from.isoformat(),
                    "effective_until": p.effective_until.isoformat() if p.effective_until else None,
                }
            )
        entry["pricing_history"] = pricing_history

        return entry

    async def search_models(self, capabilities: list[str]) -> list[dict[str, Any]]:
        """Search models by capabilities.

        Args:
            capabilities: List of required capability tags.

        Returns:
            List of catalog entries matching all capabilities.
        """
        stmt = (
            select(ModelRegistryModel, ModelProviderModel)
            .join(ModelProviderModel, ModelRegistryModel.provider_id == ModelProviderModel.id)
            .where(
                ModelRegistryModel.deleted.is_(False),
                ModelRegistryModel.is_enabled.is_(True),
            )
        )
        result = await self._db.execute(stmt)
        rows = result.all()

        items = []
        for registry, provider in rows:
            model_caps = registry.capabilities or {}
            if all(model_caps.get(cap) for cap in capabilities):
                items.append(await self._build_catalog_entry(registry, provider))

        return items

    async def compare_models(self, model_ids: list[str]) -> list[dict[str, Any]]:
        """Compare multiple models side-by-side.

        Args:
            model_ids: List of model identifiers to compare.

        Returns:
            List of catalog entries for comparison.
        """
        items = []
        for model_id in model_ids:
            entry = await self.get_model(model_id)
            if entry:
                items.append(entry)
        return items

    async def _build_catalog_entry(
        self,
        registry: ModelRegistryModel,
        provider: ModelProviderModel,
    ) -> dict[str, Any]:
        """Build a catalog entry from registry and provider models."""
        capabilities = registry.capabilities or {}
        capability_badges = [k for k, v in capabilities.items() if v is True]

        # Get effective pricing
        now = datetime.now(UTC)
        pricing_stmt = (
            select(ModelPricingModel)
            .where(
                ModelPricingModel.model_id == registry.model_id,
                ModelPricingModel.effective_from <= now,
                ModelPricingModel.deleted.is_(False),
            )
            .order_by(ModelPricingModel.effective_from.desc())
            .limit(1)
        )
        pricing_result = await self._db.execute(pricing_stmt)
        pricing = pricing_result.scalar_one_or_none()

        effective_pricing = None
        has_pricing = False
        if pricing and (pricing.effective_until is None or pricing.effective_until > now):
            effective_pricing = {
                "input_per_1k": pricing.input_price_per_1k,
                "output_per_1k": pricing.output_price_per_1k,
                "currency": pricing.currency,
            }
            has_pricing = True

        return {
            "model_id": registry.model_id,
            "display_name": registry.display_name,
            "provider_name": provider.name,
            "provider_display_name": provider.display_name,
            "provider_status": provider.status,
            "model_type": registry.model_type,
            "max_context": registry.max_context,
            "capabilities": capabilities,
            "capability_badges": capability_badges,
            "effective_pricing": effective_pricing,
            "has_pricing": has_pricing,
            "is_custom": registry.is_custom,
            "is_enabled": registry.is_enabled,
            "created_at": registry.created_at.isoformat() if registry.created_at else None,
        }
