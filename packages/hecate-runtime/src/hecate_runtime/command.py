"""Re-export convenience for the Command type.

This module re-exports ``Command`` from ``hecate_runtime.types`` so that
consumers can import it as ``from hecate_runtime.command import Command`` for
readability, without needing to know the internal types module layout.
"""

from __future__ import annotations

from hecate_runtime.types import Command

__all__ = ["Command"]
