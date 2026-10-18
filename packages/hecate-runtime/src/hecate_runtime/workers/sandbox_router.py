"""Compatibility forwarding for the shared sandbox enforcement router."""

import sys
from typing import TYPE_CHECKING

import hecate_runtime.sandbox_router as _mod

if TYPE_CHECKING:
    from hecate_runtime.sandbox_router import *  # noqa: F401,F403

sys.modules[__name__] = _mod
