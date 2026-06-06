"""Serper (Google) search provider — requires API key."""

from __future__ import annotations

import logging

import httpx

from hecate.services.tool.search import SearchProvider

logger = logging.getLogger(__name__)

_SERPER_URL = "https://google.serper.dev/search"


class SerperSearchProvider(SearchProvider):
    """Search provider using the Serper (Google Search) API.

    Requires a Serper API key passed at construction time.
    """

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def search(self, query: str, max_results: int = 5) -> list[dict]:
        """Search via Serper API and return structured results.

        Args:
            query: The search query string.
            max_results: Maximum number of results to return.

        Returns:
            List of dicts with title, url, snippet.
        """
        async with httpx.AsyncClient() as client:
            response = await client.post(
                _SERPER_URL,
                json={"q": query, "num": max_results},
                headers={"X-API-KEY": self._api_key, "Content-Type": "application/json"},
                timeout=10.0,
            )
            response.raise_for_status()
            data = response.json()

        results: list[dict] = []
        for item in data.get("organic", []):
            results.append(
                {
                    "title": item.get("title", ""),
                    "url": item.get("link", ""),
                    "snippet": item.get("snippet", ""),
                }
            )
        return results
