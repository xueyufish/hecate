"""Tests for search provider factory and interface."""

from __future__ import annotations

import pytest

from hecate.services.tool.search import SearchProvider
from hecate.services.tool.search.duckduckgo import DuckDuckGoSearchProvider
from hecate.services.tool.search.factory import create_search_provider
from hecate.services.tool.search.serper import SerperSearchProvider
from hecate.services.tool.search.tavily import TavilySearchProvider


class TestSearchProviderABC:
    """Verify SearchProvider cannot be instantiated directly."""

    def test_abc_not_instantiable(self) -> None:
        with pytest.raises(TypeError):
            SearchProvider()  # type: ignore[abstract]


class TestCreateSearchProvider:
    """Test factory function routing."""

    def test_duckduckgo_default(self) -> None:
        provider = create_search_provider("duckduckgo")
        assert isinstance(provider, DuckDuckGoSearchProvider)

    def test_tavily_with_key(self) -> None:
        provider = create_search_provider("tavily", api_key="test-key")
        assert isinstance(provider, TavilySearchProvider)

    def test_tavily_without_key_raises(self) -> None:
        with pytest.raises(ValueError, match="SEARCH_API_KEY"):
            create_search_provider("tavily")

    def test_serper_with_key(self) -> None:
        provider = create_search_provider("serper", api_key="test-key")
        assert isinstance(provider, SerperSearchProvider)

    def test_serper_without_key_raises(self) -> None:
        with pytest.raises(ValueError, match="SEARCH_API_KEY"):
            create_search_provider("serper")

    def test_unknown_provider_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown search provider"):
            create_search_provider("bing")

    def test_case_insensitive(self) -> None:
        provider = create_search_provider("DuckDuckGo")
        assert isinstance(provider, DuckDuckGoSearchProvider)
