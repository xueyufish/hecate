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


async def test_update_installs_invalid_leases_for_dispatch_boundary_refusal():
    """Only a consumed-nonce replay is refused at install (step6b replay
    window). Every other invalidity installs and is refused at the dispatch
    boundary with its precise reason — the dispatch-time validation contract
    and its refusal evidence stay untouched."""
    now = {"t": datetime.now(UTC)}
    gate = LeaseGate(SECRET, deployment_domain=DEPLOYMENT, clock=lambda: now["t"])
    gate.update(_lease(scope=["domain_a"]))
    assert gate.update(_lease(ttl_seconds=-1)) is True
    with pytest.raises(CredentialError, match="expired"):
        await gate.check()
    foreign = lease_claims(
        SECRET, iss=ISSUER, deployment_domain="other-root", sub="host-1", ttl_seconds=3600, scope=["domain_a"]
    )[0]
    assert gate.update(foreign) is True
    with pytest.raises(CredentialError, match="does not bind"):
        await gate.check()
    token = _lease(scope=["domain_a"])
    gate.update({**token, "scope": ["domain_b"]})
    with pytest.raises(CredentialError, match="signature"):
        await gate.check()


async def test_empty_gate_refuses():
    gate = LeaseGate(SECRET, deployment_domain=DEPLOYMENT)
    with pytest.raises(CredentialError, match="no authorization lease"):
        await gate.check()


async def test_tampered_scope_ride_refused_at_dispatch():
    now = {"t": datetime.now(UTC)}
    gate = LeaseGate(SECRET, deployment_domain=DEPLOYMENT, clock=lambda: now["t"])
    token = _lease(scope=["domain_a"])
    tampered = {**token, "scope": ["domain_b", "domain_c"]}
    gate.update(tampered)  # installs (legacy posture); dispatch refuses
    with pytest.raises(CredentialError, match="signature"):
        await gate.check()


@pytest.mark.parametrize("field", ["iss", "sub", "tenant"])
async def test_signed_foreign_host_identity_is_rejected(field):
    """A valid signature cannot substitute another enrolled identity; the
    foreign lease installs (legacy posture) and the dispatch boundary
    refuses it with the identity reason."""
    claims = {"iss": ISSUER, "sub": "host-1", "tenant": "workspace-1"}
    claims[field] = "foreign"
    token, _ = lease_claims(SECRET, deployment_domain=DEPLOYMENT, ttl_seconds=60, **claims)
    gate = LeaseGate(
        SECRET,
        deployment_domain=DEPLOYMENT,
        issuer_domain=ISSUER,
        host_id="host-1",
        workspace_id="workspace-1",
    )
    assert gate.update(token) is True
    assert gate.active is False
    with pytest.raises(CredentialError, match="enrolled host"):
        await gate.check()


async def test_update_refuses_replayed_nonce():
    now = {"t": datetime.now(UTC)}
    gate = LeaseGate(SECRET, deployment_domain=DEPLOYMENT, clock=lambda: now["t"])
    token = _lease(scope=["domain_a"])
    assert gate.update(token) is True
    await gate.check()  # consumes the nonce
    assert gate.update(token) is False  # the same lease cannot re-arm
    with pytest.raises(CredentialError, match="nonce"):
        await gate.check()


async def test_consumed_nonces_survive_restart(tmp_path):
    """The nonce record is the replay memory across host restarts."""
    now = {"t": datetime.now(UTC)}
    record = tmp_path / "lease-consumed-nonces.jsonl"
    gate = LeaseGate(SECRET, deployment_domain=DEPLOYMENT, clock=lambda: now["t"], nonce_record_path=str(record))
    token = _lease(scope=["domain_a"])
    assert gate.update(token) is True
    await gate.check()
    assert record.exists()

    restarted = LeaseGate(SECRET, deployment_domain=DEPLOYMENT, clock=lambda: now["t"], nonce_record_path=str(record))
    assert restarted.update(token) is False  # replay refused across the restart
    fresh = _lease(scope=["domain_a"])  # new nonce
    assert restarted.update(fresh) is True
    claims = await restarted.check()
    assert claims.scope == ["domain_a"]


async def test_expired_nonce_entries_are_pruned_on_load(tmp_path):
    now = {"t": datetime.now(UTC)}
    record = tmp_path / "lease-consumed-nonces.jsonl"
    record.write_text(
        "\n".join(
            [
                '{"nonce": "stale", "exp": "2020-01-01T00:00:00+00:00"}',
                "not-json-at-all",
                '{"nonce": "live", "exp": "2999-01-01T00:00:00+00:00"}',
            ]
        ),
        encoding="utf-8",
    )
    gate = LeaseGate(SECRET, deployment_domain=DEPLOYMENT, clock=lambda: now["t"], nonce_record_path=str(record))
    assert gate._consumed == {"live"}
    now["t"] = now["t"].replace(year=3000)
    later = LeaseGate(SECRET, deployment_domain=DEPLOYMENT, clock=lambda: now["t"], nonce_record_path=str(record))
    assert later._consumed == set()


async def test_nonce_write_failure_refuses_authorization(tmp_path):
    """A failing record write is conservative: no authorization without the
    replay memory (the nonce is consumed either way — retry is a replay)."""
    now = {"t": datetime.now(UTC)}
    gate = LeaseGate(SECRET, deployment_domain=DEPLOYMENT, clock=lambda: now["t"], nonce_record_path=str(tmp_path))
    gate.update(_lease(scope=["domain_a"]))
    with pytest.raises(CredentialError, match="persistence failed"):
        await gate.check()
