"""Factory for creating search provider instances based on configuration."""

from __future__ import annotations

from hecate.services.tool.search import SearchProvider
from hecate.services.tool.search.duckduckgo import DuckDuckGoSearchProvider
from hecate.services.tool.search.serper import SerperSearchProvider
from hecate.services.tool.search.tavily import TavilySearchProvider


def create_search_provider(provider: str = "duckduckgo", api_key: str = "") -> SearchProvider:
    """Create a SearchProvider instance based on provider name.

    Args:
        provider: Provider name — "duckduckgo", "tavily", or "serper".
        api_key: API key for providers that require one.

    Returns:
        A SearchProvider instance.

    Raises:
        ValueError: If the provider requires an API key and none is provided,
            or if the provider name is unknown.
    """
    provider_lower = provider.lower()

    if provider_lower == "duckduckgo":
        return DuckDuckGoSearchProvider()

    if provider_lower == "tavily":
        if not api_key:
            raise ValueError("Tavily search provider requires SEARCH_API_KEY to be set")
        return TavilySearchProvider(api_key=api_key)

    if provider_lower == "serper":
        if not api_key:
            raise ValueError("Serper search provider requires SEARCH_API_KEY to be set")
        return SerperSearchProvider(api_key=api_key)

    raise ValueError(f"Unknown search provider: {provider!r}. Supported: duckduckgo, tavily, serper")
