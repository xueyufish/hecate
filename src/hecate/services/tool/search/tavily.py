"""Tavily search provider — requires API key."""

from __future__ import annotations

import logging

from hecate.services.tool.search import SearchProvider

logger = logging.getLogger(__name__)


class TavilySearchProvider(SearchProvider):
    """Search provider using the Tavily search API.

    Requires a Tavily API key passed at construction time.
    """

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def search(self, query: str, max_results: int = 5) -> list[dict]:
        """Search via Tavily API and return structured results.

        Args:
            query: The search query string.
            max_results: Maximum number of results to return.

        Returns:
            List of dicts with title, url, snippet.
        """
        from tavily import AsyncTavilyClient

        client = AsyncTavilyClient(api_key=self._api_key)
        response = await client.search(query, max_results=max_results)

        results: list[dict] = []
        for item in response.get("results", []):
            results.append(
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "snippet": item.get("content", ""),
                }
            )
        return results
