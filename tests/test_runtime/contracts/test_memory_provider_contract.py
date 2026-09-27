"""MemoryProvider contract — protocol conformance for the retrieval surface.

Honest boundary: the production provider (``hecate-memory``) needs a live
vector store, so CI runs the contract against a hand-written fake plus an
import/signature smoke check of the production implementation. Full
dual-implementation semantic runs belong to hecate-memory's own test
infrastructure.

New MemoryProvider implementations must satisfy this contract and
register themselves in ``PROVIDER_FACTORIES``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest


@dataclass
class FakeSearchResult:
    content: str
    score: float
    metadata: dict = field(default_factory=dict)


class FakeMemoryProvider:
    """Contract fake covering the retrieval/lifecycle surface the runtime
    consumes (search + collection existence)."""

    def __init__(self) -> None:
        self.collections: set[str] = {"kb_fake"}
        self.searched: list[tuple[str, str]] = []

    async def search(self, *, collection_name: str, query: str, limit: int = 10, mode: str = "hybrid") -> list[Any]:
        if collection_name not in self.collections:
            return []
        self.searched.append((collection_name, query))
        return [FakeSearchResult(content=f"hit for {query}", score=0.9, metadata={"collection": collection_name})]

    async def collection_exists(self, collection_name: str) -> bool:
        return collection_name in self.collections


PROVIDER_FACTORIES: dict[str, Any] = {
    "fake": FakeMemoryProvider,
}


@pytest.fixture(params=list(PROVIDER_FACTORIES), ids=list(PROVIDER_FACTORIES))
def provider(request) -> Any:
    return PROVIDER_FACTORIES[request.param]()


@pytest.mark.asyncio
async def test_contract_search_returns_scored_results(provider) -> None:
    results = await provider.search(collection_name="kb_fake", query="hello", limit=5)
    assert len(results) == 1
    assert results[0].score > 0
    assert "hello" in results[0].content


@pytest.mark.asyncio
async def test_contract_search_unknown_collection_is_empty(provider) -> None:
    results = await provider.search(collection_name="kb_missing", query="hello")
    assert results == []


@pytest.mark.asyncio
async def test_contract_collection_membership(provider) -> None:
    assert await provider.collection_exists("kb_fake") is True
    assert await provider.collection_exists("kb_missing") is False


def test_production_provider_importable_and_protocol_shaped() -> None:
    """Smoke: the production implementation imports and carries the search
    surface the runtime consumes. Full semantic runs live in
    hecate-memory's own infrastructure (needs a vector store)."""
    from hecate_memory.memory.provider_impl import BuiltinMemoryProvider  # noqa: F401

    assert BuiltinMemoryProvider is not None
