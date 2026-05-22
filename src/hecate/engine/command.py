"""Re-export convenience for the Command type.

This module re-exports ``Command`` from ``hecate.engine.types`` so that
consumers can import it as ``from hecate.engine.command import Command`` for
readability, without needing to know the internal types module layout.
"""

from __future__ import annotations

from hecate.engine.types import Command

__all__ = ["Command"]
