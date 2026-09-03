"""Vault registration — entry_point-driven secret provider discovery.

Per PR1.2, secret providers (HashiCorp / AWS / Azure) are discovered via
the ``hecate.secret_providers`` entry-point group on the host. Each
provider module exposes a zero-arg ``provider()`` factory that reads its
own settings and returns an instance, or ``None`` when unconfigured.
This module just hosts the function that main.py calls at lifespan
startup; the actual provider construction happens in the entry-point
factories (hecate_enterprise.vault.hcvault_provider, etc.).
"""

from __future__ import annotations

import logging

from hecate.enterprise.vault.resolver import load_entry_point_providers, register_providers

logger = logging.getLogger(__name__)


def register_secret_providers() -> int:
    """Discover and register secret providers via entry points.

    Returns:
        Number of providers registered.
    """
    providers = load_entry_point_providers()
    register_providers(*providers)
    logger.info("Registered %d secret providers", len(providers))
    return len(providers)
