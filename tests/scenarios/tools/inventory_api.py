"""Deterministic stub inventory business API for the standalone-consumption pack.

Stands in for the business App's structured inventory API required by the plan's
SC scenarios (SC02: structured inventory read & unauthorized call). The fixture
owns the business rules itself — the business App keeps final authority over its
own state machine and permissions, so Hecate implements no inventory, pricing,
or customer management. Inventory is only an example business domain; the stub's
shape (two isolated data domains, read-only identities, approval-gated writes)
is what SC scenarios actually rely on.

Every invocation is recorded as (operation, domain, identity, outcome) so
scenario tests can assert authorization results and side-effect counts
deterministically. Denial outcomes (``denied_cross_domain`` /
``denied_read_only`` / ``denied_unapproved``) are the queryable rejection
evidence the platform-scenario-pack spec requires.

Approvals are one-shot and parameter-bound: a write only executes when the
exact (domain, sku, quantity) tuple was approved and the approval has not been
consumed yet, so an approved write executes exactly once and a repeated call
never produces a second change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class InventoryOutcome(StrEnum):
    """Deterministic result of one invocation against the stub API."""

    OK = "ok"
    DENIED_CROSS_DOMAIN = "denied_cross_domain"
    DENIED_READ_ONLY = "denied_read_only"
    DENIED_UNAPPROVED = "denied_unapproved"


@dataclass
class InventoryCall:
    """One recorded invocation with its authorization result."""

    operation: str
    domain: str
    identity: str
    outcome: InventoryOutcome
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InventoryIdentity:
    """A business App identity: which domains it may see, whether it may write."""

    name: str
    allowed_domains: frozenset[str]
    can_write: bool = False


@dataclass
class StubInventoryApi:
    """In-memory stand-in for a real structured inventory backend.

    Two isolated data domains carry disjoint synthetic records; identities are
    bound to specific domains, and writes require a matching parameter-bound
    approval that is consumed on first use.
    """

    def __init__(self, domains: dict[str, dict[str, dict[str, Any]]] | None = None) -> None:
        self._domains: dict[str, dict[str, dict[str, Any]]] = (
            domains
            if domains is not None
            else {
                "domain_a": {
                    "SKU-A1": {"name": "示例商品A1", "quantity": 100},
                    "SKU-A2": {"name": "示例商品A2", "quantity": 40},
                },
                "domain_b": {
                    "SKU-B1": {"name": "示例商品B1", "quantity": 7},
                },
            }
        )
        self._calls: list[InventoryCall] = []
        self._approvals: set[tuple[str, str, int]] = set()

    def make_read_only_identity(self, name: str, domain: str) -> InventoryIdentity:
        """Read-only identity bound to a single domain."""
        return InventoryIdentity(name=name, allowed_domains=frozenset({domain}), can_write=False)

    def make_writer_identity(self, name: str, *domains: str) -> InventoryIdentity:
        """Write-capable identity bound to the given domains."""
        return InventoryIdentity(name=name, allowed_domains=frozenset(domains), can_write=True)

    def approve_write(self, domain: str, sku: str, quantity: int) -> None:
        """Grant a one-shot approval bound to the exact write parameters."""
        self._approvals.add((domain, sku, quantity))

    def read(self, domain: str, sku: str, identity: InventoryIdentity) -> dict[str, Any] | None:
        """Structured read inside the identity's domain; cross-domain is denied."""
        if domain not in identity.allowed_domains:
            self._record("read", domain, identity, InventoryOutcome.DENIED_CROSS_DOMAIN)
            return None
        record = self._domains.get(domain, {}).get(sku)
        self._record("read", domain, identity, InventoryOutcome.OK, sku=sku, record=record)
        return record

    def write(self, domain: str, sku: str, quantity: int, identity: InventoryIdentity) -> InventoryOutcome:
        """Approval-gated write; every denial path leaves state untouched."""
        if domain not in identity.allowed_domains:
            return self._record("write", domain, identity, InventoryOutcome.DENIED_CROSS_DOMAIN, sku=sku)
        if not identity.can_write:
            return self._record("write", domain, identity, InventoryOutcome.DENIED_READ_ONLY, sku=sku)
        if (domain, sku, quantity) not in self._approvals:
            return self._record("write", domain, identity, InventoryOutcome.DENIED_UNAPPROVED, sku=sku)
        self._approvals.discard((domain, sku, quantity))
        self._domains[domain][sku]["quantity"] = quantity
        return self._record("write", domain, identity, InventoryOutcome.OK, sku=sku, quantity=quantity)

    def quantity(self, domain: str, sku: str) -> int | None:
        """Current quantity of one record (fixture introspection, not a call)."""
        record = self._domains.get(domain, {}).get(sku)
        return None if record is None else record["quantity"]

    def calls(self) -> list[InventoryCall]:
        """All recorded invocations in order."""
        return list(self._calls)

    def calls_by_outcome(self, outcome: InventoryOutcome) -> list[InventoryCall]:
        """Queryable denial/success evidence."""
        return [call for call in self._calls if call.outcome == outcome]

    def _record(
        self,
        operation: str,
        domain: str,
        identity: InventoryIdentity,
        outcome: InventoryOutcome,
        **detail: Any,
    ) -> InventoryOutcome:
        call = InventoryCall(
            operation=operation,
            domain=domain,
            identity=identity.name,
            outcome=outcome,
            detail=dict(detail),
        )
        self._calls.append(call)
        return outcome
