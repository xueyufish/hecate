"""Tests for the Milvus and Weaviate vector store adapters (3.1.7).

The adapters lazily import their SDKs; when the SDK is absent the client
degrades to the ``"mock"`` sentinel (same contract as Chroma). Tests pin
the mock path hermetically — no Milvus/Weaviate server or SDK required.
"""

from __future__ import annotations

import pytest
from hecate_memory.rag.factory import get_vector_store
from hecate_memory.rag.milvus_store import MilvusVectorStore
from hecate_memory.rag.weaviate_store import WeaviateVectorStore

# --- factory wiring ---


class TestFactoryBackends:
    def _patch(self, monkeypatch, **attrs):
        from hecate.core.config import settings

        for key, value in attrs.items():
            monkeypatch.setattr(settings, key, value)

    def test_milvus_backend(self, monkeypatch):
        self._patch(monkeypatch, VECTOR_STORE_TYPE="milvus", MILVUS_URI="http://m:19530", MILVUS_TOKEN="")
        store = get_vector_store()
        assert isinstance(store, MilvusVectorStore)
        assert store.uri == "http://m:19530"
        assert store.token == ""

    def test_weaviate_backend(self, monkeypatch):
        self._patch(
            monkeypatch,
            VECTOR_STORE_TYPE="weaviate",
            WEAVIATE_URL="https://w:9443",
            WEAVIATE_GRPC_HOST="w:9444",
            WEAVIATE_API_KEY="k",
        )
        store = get_vector_store()
        assert isinstance(store, WeaviateVectorStore)
        assert store.http_host == "w"
        assert store.http_port == 9443
        assert store.http_secure is True
        assert store.grpc_port == "9444"
        assert store.api_key == "k"

    def test_unknown_backend_raises(self, monkeypatch):
        self._patch(monkeypatch, VECTOR_STORE_TYPE="faiss")
        with pytest.raises(ValueError, match="faiss"):
            get_vector_store()


# --- Milvus adapter (mock client path) ---


class TestMilvusMockPath:
    def _store(self) -> MilvusVectorStore:
        store = MilvusVectorStore(uri="http://localhost:19530")
        store._client = "mock"  # noqa: SLF001 — hermetic mock sentinel
        return store

    async def test_create_collection(self):
        assert await self._store().create_collection("kb1", vector_size=8) is True

    async def test_delete_and_exists(self):
        store = self._store()
        assert await store.delete_collection("kb1") is True
        assert await store.collection_exists("kb1") is True

    async def test_upsert_warns_on_sparse(self):
        store = self._store()
        assert await store.upsert("kb1", ["a"], [[0.1]], [{"text": "t"}], sparse_vectors={0: 1.0}) is True

    async def test_search_dense_mock_results(self):
        store = self._store()
        results = await store.search_dense("kb1", [0.1, 0.2], limit=5)
        assert 0 < len(results) <= 3
        assert results[0].score >= results[-1].score

    async def test_search_sparse_empty(self):
        assert await self._store().search_sparse("kb1", {0: 1.0}) == []

    async def test_count_and_scroll(self):
        store = self._store()
        assert await store.count("kb1") == 42
        results, next_offset = await store.scroll("kb1", limit=2)
        assert results
        assert next_offset is None


# --- Weaviate adapter (mock client path) ---


class TestWeaviateMockPath:
    def _store(self) -> WeaviateVectorStore:
        store = WeaviateVectorStore(url="http://localhost:8080")
        store._client = "mock"  # noqa: SLF001
        return store

    async def test_create_delete_exists(self):
        store = self._store()
        assert await store.create_collection("kb1") is True
        assert await store.delete_collection("kb1") is True
        assert await store.collection_exists("kb1") is True

    async def test_upsert_and_search(self):
        store = self._store()
        assert await store.upsert("kb1", ["a"], [[0.1]], [{"text": "t"}]) is True
        results = await store.search_dense("kb1", [0.1], limit=2)
        assert results
        assert await store.search_sparse("kb1", {0: 1.0}) == []

    async def test_count_and_scroll(self):
        store = self._store()
        assert await store.count("kb1") == 42
        results, next_offset = await store.scroll("kb1", limit=20)
        assert results
        assert next_offset is None


class TestWeaviateUrlParsing:
    def test_plain_http_default_port(self):
        store = WeaviateVectorStore(url="http://weaviate:8080", grpc_host="weaviate:50051")
        assert (store.http_host, store.http_port, store.http_secure) == ("weaviate", 8080, False)
        assert (store.grpc_host, store.grpc_port) == ("weaviate", "50051")

    def test_https_default_port(self):
        store = WeaviateVectorStore(url="https://weaviate.example.com", grpc_host="weaviate.example.com")
        assert store.http_port == 443
        assert store.http_secure is True
        assert store.grpc_port == "443"

    def test_scheme_less_url(self):
        store = WeaviateVectorStore(url="weaviate:9000")
        assert (store.http_host, store.http_port) == ("weaviate", 9000)
