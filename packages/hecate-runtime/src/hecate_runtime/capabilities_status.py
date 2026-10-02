"""Standalone-profile capability status (step5b).

Reports which optional kernel capabilities are currently usable, based on
installed distributions (extras) and installed provider sources. Assemblers
(platform composition root or a standalone host) call this at startup and
surface ``unsupported`` entries in their capability declarations instead of
discovering them as runtime ImportErrors.
"""

from __future__ import annotations

from typing import Literal

from hecate_runtime.memory import resolve_memory_provider

CapabilityState = Literal["available", "unsupported"]

try:  # pragma: no cover - importlib.metadata is always present on 3.12+
    from importlib.util import find_spec

    def _dist_available(import_root: str) -> bool:
        try:
            return find_spec(import_root) is not None
        except (ImportError, ValueError, ModuleNotFoundError):
            return False
except ImportError:  # pragma: no cover
    def _dist_available(import_root: str) -> bool:
        return False


def capability_status() -> dict[str, CapabilityState]:
    """Return per-capability availability for the running environment.

    Keys and their detection basis:

    - ``memory_consolidation``: ``hecate_memory`` importable (extra
      ``memory``) — compaction/context consolidation backends.
    - ``sandbox_offload``: ``hecate_sandbox`` importable (extra ``sandbox``)
      — offload execution environments.
    - ``memory_provider``: a memory provider source is installed and
      resolves to a provider (platform bridge or host adapter).
    - ``task_memory_hooks``: memory provider present (hooks degrade to
      no-op otherwise, by contract).
    - ``a2a_handoff``: never auto-available — the transport lives in the
      platform; a host must inject it. Detected here only as
      ``unsupported``.
    """
    return {
        "memory_consolidation": "available" if _dist_available("hecate_memory") else "unsupported",
        "sandbox_offload": "available" if _dist_available("hecate_sandbox") else "unsupported",
        "memory_provider": "available" if resolve_memory_provider() is not None else "unsupported",
        "task_memory_hooks": "available" if resolve_memory_provider() is not None else "unsupported",
        "a2a_handoff": "unsupported",
    }
