"""DuckDuckGo search provider — default, no API key required."""

from __future__ import annotations

import logging

from hecate.services.tool.search import SearchProvider

logger = logging.getLogger(__name__)


class DuckDuckGoSearchProvider(SearchProvider):
    """Search provider using the DuckDuckGo search engine.

    No API key required. Uses the ``duckduckgo-search`` package.
    """

    async def search(self, query: str, max_results: int = 5) -> list[dict]:
        """Search DuckDuckGo and return structured results.

        Args:
            query: The search query string.
            max_results: Maximum number of results to return.

        Returns:
            List of dicts with title, url, snippet.
        """
        from duckduckgo_search import DDGS

        results: list[dict] = []
        with DDGS() as ddgs:
            for item in ddgs.text(query, max_results=max_results):
                results.append(
                    {
                        "title": item.get("title", ""),
                        "url": item.get("href", ""),
                        "snippet": item.get("body", ""),
                    }
                )
        return results
