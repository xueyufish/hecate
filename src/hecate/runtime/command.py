"""Re-export convenience for the Command type.

This module re-exports ``Command`` from ``hecate.runtime.types`` so that
consumers can import it as ``from hecate.runtime.command import Command`` for
readability, without needing to know the internal types module layout.
"""

from __future__ import annotations

from hecate.runtime.types import Command

__all__ = ["Command"]
