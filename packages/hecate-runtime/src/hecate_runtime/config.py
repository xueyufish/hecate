"""Kernel-owned runtime configuration (step5b).

The kernel never reads platform settings (``hecate.core.config``). Instead,
the caller — the platform composition root or a standalone host — builds a
``RuntimeConfig`` and installs it via :func:`configure_kernel`; the defaults
mirror the platform settings so a bare kernel behaves like an unconfigured
platform installation.

Configuration is process-level, exactly like the platform settings singleton
it replaces: concurrent runs share one config. Per-run configuration is not
supported (same semantics as before the extraction).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeConfig:
    """Flags and secrets the kernel consumes, mirroring platform settings.

    Field defaults match the platform ``Settings`` defaults (see
    ``hecate.core.config``) so that a kernel assembled without explicit
    configuration behaves like a platform installation with default flags.
    """

    # Task Memory / reflection master switch (settings.REFLECTION_ENABLED).
    reflection_enabled: bool = False
    # L2 compaction flush window upper bound (settings.MEMORY_FLUSH_ENABLED).
    memory_flush_enabled: bool = False
    # Memory pressure alert hint injection (settings.MEMORY_PRESSURE_NUDGE_ENABLED).
    memory_pressure_nudge_enabled: bool = False
    # Pre-LLM memory context block (settings.MEMORY_PREFETCH_ENABLED).
    memory_prefetch_enabled: bool = False
    memory_prefetch_max_entries: int = 5
    memory_prefetch_max_tokens: int = 500
    # llm-guard input/output scanning toggle (settings.LLM_GUARD_ENABLED).
    llm_guard_enabled: bool = True
    # Fernet key for PII mask-and-encrypt mode (settings.FERNET_KEY).
    fernet_key: str = ""


_current: RuntimeConfig = RuntimeConfig()


def configure_kernel(config: RuntimeConfig) -> RuntimeConfig:
    """Install ``config`` as the process-wide kernel configuration.

    Returns the previously installed config so callers can restore it in
    tests.
    """
    global _current
    previous = _current
    _current = config
    return previous


def kernel_config() -> RuntimeConfig:
    """Return the currently installed kernel configuration."""
    return _current


def reset_kernel_config() -> None:
    """Restore the default configuration (test helper)."""
    global _current
    _current = RuntimeConfig()
