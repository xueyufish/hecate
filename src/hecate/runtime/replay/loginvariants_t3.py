"""Compatibility shim: implementation moved to ``hecate_runtime`` (step5b).

Forwarding only — slated for removal in step19
(``runtime-standalone-distribution`` -> ``legacy-platform-consolidation``).
"""

import sys
from typing import TYPE_CHECKING

import hecate_runtime.replay.loginvariants_t3 as _mod

if TYPE_CHECKING:
    from hecate_runtime.replay.loginvariants_t3 import *  # noqa: F401,F403

sys.modules[__name__] = _mod
