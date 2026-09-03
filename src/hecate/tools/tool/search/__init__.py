"""Search provider abstraction for web_search built-in tool.

Defines the SearchProvider ABC and a factory function for creating
provider instances based on environment configuration.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class SearchProvider(ABC):
    """Abstract interface for web search providers.

    Implementations wrap external search APIs (Tavily, Serper, DuckDuckGo)
    behind a uniform interface that returns structured results.
    """

    @abstractmethod
    async def search(self, query: str, max_results: int = 5) -> list[dict]:
        """Search the web and return structured results.

        Args:
            query: The search query string.
            max_results: Maximum number of results to return.

        Returns:
            List of dicts with keys: title (str), url (str), snippet (str).
        """
        ...
