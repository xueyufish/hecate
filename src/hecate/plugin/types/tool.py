"""ToolPluginABC — abstract base class for tool-type plugins."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ToolPluginABC(ABC):
    """Base class for plugins that add callable tools to Agents.

    Subclasses must define name, description, and an async execute method.
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @abstractmethod
    async def execute(self, params: dict[str, Any]) -> dict[str, Any]: ...
