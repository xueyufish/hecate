"""Delegation scope intersection (step7 local slice).

A sub-delegated request's authority is the intersection of the parent grant,
team/organization policy, target resource policy and the task's own scope —
never wider than any of them, default deny. Every denial is traceable to the
delegation chain that lacked the permission.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class DelegationLink:
    """One hop of a delegation chain.

    ``domains`` is the data-domain grant of this hop; the effective grant of a
    chain is the intersection across hops.
    """

    delegation_id: str
    grantor: str
    grantee: str
    domains: tuple[str, ...]
    task_scope: str | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True)
class DelegationDecision:
    """Result of intersecting a chain with a request."""

    allowed: bool
    effective_domains: tuple[str, ...] = ()
    reason: str = ""


def effective_delegation_domains(
    links: tuple[DelegationLink, ...],
    requested_domains: tuple[str, ...],
    *,
    now: datetime,
) -> DelegationDecision:
    """Intersect the chain grants with the request; default deny.

    Every hop must be unexpired and its domains must cover the request — the
    effective set is what remains. An empty chain denies (no delegation, no
    grant); a missing permission is reported against the full chain so the
    refusal is traceable.
    """
    if not links:
        return DelegationDecision(False, (), "no delegation chain: sub-delegation denied by default")
    chain_ids = [link.delegation_id for link in links]
    effective: set[str] | None = None
    for link in links:
        if link.expires_at is not None and now >= link.expires_at:
            return DelegationDecision(
                False,
                (),
                f"delegation {link.delegation_id} (chain {chain_ids}) has expired",
            )
        if effective is None:
            effective = set(link.domains)
        else:
            effective &= set(link.domains)
    requested = set(requested_domains)
    covered = requested & (effective or set())
    missing = requested - (effective or set())
    if missing:
        return DelegationDecision(
            False,
            tuple(sorted(covered)),
            (
                f"delegation chain {chain_ids} does not grant domains "
                f"{sorted(missing)}; grant intersection is {sorted(effective or set())}"
            ),
        )
    return DelegationDecision(True, tuple(sorted(covered)))
