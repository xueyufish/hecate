"""Web crawler for fetching and extracting content from URLs.

Fetches URL content using httpx, extracts text from HTML using BeautifulSoup,
and extracts metadata (title, description). Feeds into the RAG pipeline via
the existing chunker/embedder/indexer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0
MAX_CONTENT_SIZE = 1_000_000  # 1MB


@dataclass
class CrawlResult:
    """Result of crawling a single URL."""

    url: str
    title: str
    description: str
    text: str
    success: bool
    error: str | None = None


class WebCrawler:
    """Fetch URLs and extract text content for RAG ingestion."""

    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT,
        max_content_size: int = MAX_CONTENT_SIZE,
    ) -> None:
        self.timeout = timeout
        self.max_content_size = max_content_size

    async def crawl_url(self, url: str) -> CrawlResult:
        """Crawl a single URL and extract text content.

        Args:
            url: The URL to crawl.

        Returns:
            CrawlResult with extracted text and metadata.
        """
        try:
            parsed = urlparse(url)
            if not parsed.scheme or not parsed.netloc:
                return CrawlResult(
                    url=url,
                    title="",
                    description="",
                    text="",
                    success=False,
                    error="Invalid URL format",
                )

            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()

            content = response.text
            if len(content) > self.max_content_size:
                logger.warning(f"Content from {url} truncated from {len(content)} to {self.max_content_size} bytes")
                content = content[: self.max_content_size]

            soup = BeautifulSoup(content, "html.parser")

            title = ""
            title_tag = soup.find("title")
            if title_tag and title_tag.string:
                title = title_tag.string.strip()

            description = ""
            meta_desc = soup.find("meta", attrs={"name": "description"})
            if meta_desc and meta_desc.get("content"):
                description = meta_desc["content"].strip()

            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()

            text = soup.get_text(separator="\n", strip=True)

            return CrawlResult(
                url=url,
                title=title,
                description=description,
                text=text,
                success=True,
            )

        except httpx.TimeoutException:
            return CrawlResult(
                url=url,
                title="",
                description="",
                text="",
                success=False,
                error=f"Timeout after {self.timeout}s",
            )
        except httpx.HTTPStatusError as e:
            return CrawlResult(
                url=url,
                title="",
                description="",
                text="",
                success=False,
                error=f"HTTP {e.response.status_code}",
            )
        except Exception as e:
            return CrawlResult(
                url=url,
                title="",
                description="",
                text="",
                success=False,
                error=str(e),
            )

    async def crawl_urls(self, urls: list[str]) -> list[CrawlResult]:
        """Crawl multiple URLs in parallel.

        Args:
            urls: List of URLs to crawl.

        Returns:
            List of CrawlResult for each URL.
        """
        import asyncio

        results = await asyncio.gather(*[self.crawl_url(url) for url in urls])
        return list(results)


web_crawler = WebCrawler()
