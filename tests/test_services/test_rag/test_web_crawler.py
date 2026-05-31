"""Tests for web crawler service and URL ingestion endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from httpx import AsyncClient

from hecate.services.rag.crawler import CrawlResult, WebCrawler


async def test_crawl_url_success() -> None:
    crawler = WebCrawler()
    mock_response = AsyncMock()
    mock_response.text = "<html><head><title>Test</title></head><body>Hello</body></html>"
    mock_response.raise_for_status = AsyncMock()

    with patch("httpx.AsyncClient.get", return_value=mock_response):
        result = await crawler.crawl_url("https://example.com")

    assert result.success is True
    assert result.title == "Test"
    assert "Hello" in result.text


async def test_crawl_url_invalid_url() -> None:
    crawler = WebCrawler()
    result = await crawler.crawl_url("not-a-url")
    assert result.success is False
    assert "Invalid URL" in result.error


async def test_crawl_url_metadata_extraction() -> None:
    crawler = WebCrawler()
    html = """
    <html>
    <head>
        <title>My Article</title>
        <meta name="description" content="Article summary">
    </head>
    <body>Content here</body>
    </html>
    """
    mock_response = AsyncMock()
    mock_response.text = html
    mock_response.raise_for_status = AsyncMock()

    with patch("httpx.AsyncClient.get", return_value=mock_response):
        result = await crawler.crawl_url("https://example.com")

    assert result.title == "My Article"
    assert result.description == "Article summary"


async def test_crawl_urls_batch() -> None:
    crawler = WebCrawler()

    async def mock_crawl(url: str) -> CrawlResult:
        if url == "https://fail.com":
            return CrawlResult(url=url, title="", description="", text="", success=False, error="Failed")
        return CrawlResult(url=url, title="Title", description="", text="Content", success=True)

    with patch.object(crawler, "crawl_url", side_effect=mock_crawl):
        results = await crawler.crawl_urls(["https://ok.com", "https://fail.com"])

    assert len(results) == 2
    assert results[0].success is True
    assert results[1].success is False


async def test_ingest_url_endpoint(client: AsyncClient) -> None:
    kb_resp = await client.post("/api/knowledge-bases", json={"name": "test-kb"})
    assert kb_resp.status_code == 201
    kb_id = kb_resp.json()["id"]

    mock_crawl = CrawlResult(
        url="https://example.com",
        title="Test Page",
        description="A test page",
        text="Hello world content",
        success=True,
    )

    with (
        patch("hecate.api.management.knowledge.web_crawler.crawl_urls", return_value=[mock_crawl]),
        patch(
            "hecate.api.management.knowledge.knowledge_base_service.ingest_document_text",
            return_value={"chunk_count": 3},
        ),
    ):
        resp = await client.post(
            f"/api/knowledge-bases/{kb_id}/urls",
            json={"url": "https://example.com"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] == 1
    assert data["failed"] == 0
    assert data["ingested"][0]["chunk_count"] == 3


async def test_ingest_url_batch_endpoint(client: AsyncClient) -> None:
    kb_resp = await client.post("/api/knowledge-bases", json={"name": "test-kb-batch"})
    assert kb_resp.status_code == 201
    kb_id = kb_resp.json()["id"]

    mock_results = [
        CrawlResult(url="https://ok.com", title="OK", description="", text="Content", success=True),
        CrawlResult(url="https://fail.com", title="", description="", text="", success=False, error="Timeout"),
    ]

    with (
        patch("hecate.api.management.knowledge.web_crawler.crawl_urls", return_value=mock_results),
        patch(
            "hecate.api.management.knowledge.knowledge_base_service.ingest_document_text",
            return_value={"chunk_count": 2},
        ),
    ):
        resp = await client.post(
            f"/api/knowledge-bases/{kb_id}/urls",
            json={"urls": ["https://ok.com", "https://fail.com"]},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] == 1
    assert data["failed"] == 1
    assert len(data["errors"]) == 1
