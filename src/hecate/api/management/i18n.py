"""i18n management API endpoints.

Provides REST endpoints for managing translations:
- POST /api/i18n/translations — upload translations
- GET /api/i18n/translations/{locale} — download translations
- GET /api/i18n/locales — list available locales
- PUT /api/i18n/translations/{locale}/{namespace} — update namespace
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from hecate.i18n.translate import get_catalog

router = APIRouter(prefix="/api/i18n", tags=["i18n"])


class TranslationUpload(BaseModel):
    """Schema for uploading translations."""

    locale: str
    namespace: str = "common"
    format: str = "json"
    content: dict[str, Any]


class LocaleInfo(BaseModel):
    """Schema for locale information."""

    locale: str
    namespaces: list[str]


@router.post("/translations", status_code=status.HTTP_201_CREATED)
async def upload_translations(body: TranslationUpload) -> dict[str, str]:
    """Upload translations for a locale and namespace."""
    catalog = get_catalog()
    catalog.set_translations(body.locale, body.namespace, body.content)
    return {"status": "ok", "locale": body.locale, "namespace": body.namespace}


@router.get("/translations/{locale}")
async def get_translations(locale: str) -> dict[str, Any]:
    """Get all translations for a locale."""
    catalog = get_catalog()
    translations = catalog.get_all(locale)
    if not translations:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {"code": "NOT_FOUND", "message": f"No translations for locale '{locale}'", "details": None}
            },
        )
    return translations


@router.get("/locales")
async def list_locales() -> list[str]:
    """List available locales."""
    catalog = get_catalog()
    return catalog.available_locales()


@router.put("/translations/{locale}/{namespace}")
async def update_namespace(locale: str, namespace: str, body: dict[str, Any]) -> dict[str, str]:
    """Update translations for a specific locale and namespace."""
    catalog = get_catalog()
    catalog.set_translations(locale, namespace, body)
    return {"status": "ok", "locale": locale, "namespace": namespace}
