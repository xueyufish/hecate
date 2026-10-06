"""LeaseGate behavior: scope round-trip, nonce replay, and expiry.

The gate is the host's only managed-authorization state; these tests pin
its local contract (the platform-side issuance and the dispatch-boundary
wiring live in the integration suites).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hecate_durable.contracts.credentials import CredentialError, lease_claims
from hecate_runner.managed import LeaseGate

SECRET = b"lease-gate-secret"
ISSUER = "lease-issuer"
DEPLOYMENT = "host-root"


def _lease(**overrides):
    return lease_claims(
        SECRET,
        iss=ISSUER,
        deployment_domain=DEPLOYMENT,
        sub="host-1",
        ttl_seconds=overrides.pop("ttl_seconds", 3600),
        **overrides,
    )[0]


async def test_check_returns_scope_and_consumes_nonce_once():
    now = {"t": datetime.now(UTC)}
    gate = LeaseGate(SECRET, deployment_domain=DEPLOYMENT, clock=lambda: now["t"])
    gate.update(_lease(scope=["domain_a"]))

    claims = await gate.check()
    assert claims.scope == ["domain_a"]
    with pytest.raises(CredentialError, match="nonce"):
        await gate.check()  # replay: the nonce was consumed


async def test_expired_lease_refuses_without_self_authorization():
    now = {"t": datetime.now(UTC)}
    gate = LeaseGate(SECRET, deployment_domain=DEPLOYMENT, clock=lambda: now["t"])
    gate.update(_lease(scope=["domain_a"], ttl_seconds=60))
    now["t"] = now["t"] + timedelta(seconds=120)
    with pytest.raises(CredentialError, match="expired"):
        await gate.check()
    assert gate.active is False


async def test_foreign_deployment_binding_refuses():
    now = {"t": datetime.now(UTC)}
    gate = LeaseGate(SECRET, deployment_domain=DEPLOYMENT, clock=lambda: now["t"])
    gate.update(_lease(scope=["domain_a"]))
    foreign = lease_claims(
        SECRET, iss=ISSUER, deployment_domain="other-root", sub="host-1", ttl_seconds=3600, scope=["domain_a"]
    )[0]
    gate.update(foreign)
    with pytest.raises(CredentialError, match="does not bind"):
        await gate.check()


async def test_empty_gate_refuses():
    gate = LeaseGate(SECRET, deployment_domain=DEPLOYMENT)
    with pytest.raises(CredentialError, match="no authorization lease"):
        await gate.check()


async def test_scope_ride_the_signature():
    now = {"t": datetime.now(UTC)}
    gate = LeaseGate(SECRET, deployment_domain=DEPLOYMENT, clock=lambda: now["t"])
    token = _lease(scope=["domain_a"])
    tampered = {**token, "scope": ["domain_b", "domain_c"]}
    gate.update(tampered)
    with pytest.raises(CredentialError, match="signature"):
        await gate.check()
