"""LeaseGate behavior: scope round-trip, nonce replay, and expiry.

The gate is the host's only managed-authorization state; these tests pin
its local contract (the platform-side issuance and the dispatch-boundary
wiring live in the integration suites).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hecate_durable.contracts.credentials import CredentialError, lease_claims
from hecate_governance.clock import TimeAnchor, TrustedClock
from hecate_runner.managed import LeaseGate, LeaseRefusedError


class _Posture:
    """Duck-typed managed lease posture (the profile's LeasePolicyConfig)."""

    def __init__(self, expected_policy_version=None, max_staleness_seconds=None):
        self.expected_policy_version = expected_policy_version
        self.max_staleness_seconds = max_staleness_seconds


SECRET = b"lease-gate-secret"
ISSUER = "lease-issuer"
DEPLOYMENT = "host-root"


@pytest.mark.parametrize("posture", [None, _Posture(expected_policy_version="v1")])
async def test_expiry_only_posture_still_rejects_clock_rollback(posture):
    wall = [datetime.now(UTC)]
    mono = [1000.0]
    gate = LeaseGate(
        SECRET,
        deployment_domain=DEPLOYMENT,
        clock=lambda: wall[0],
        lease_policy=posture,
        trusted_clock=TrustedClock(now_fn=lambda: wall[0], monotonic_fn=lambda: mono[0]),
    )
    gate.update(_lease(policy_version="v1", now=wall[0]))
    wall[0] -= timedelta(hours=1)
    mono[0] += 3700
    with pytest.raises(CredentialError, match="time cannot be trusted"):
        await gate.check()
    assert not gate._consumed


@pytest.mark.parametrize("iat", [None, "invalid"])
async def test_window_requires_valid_signed_issue_time(iat):
    import hashlib
    import hmac

    from hecate_durable.contracts.credentials import canonical_claims_digest

    token = _lease()
    token.pop("hmac")
    token.pop("iat")
    if iat is not None:
        token["iat"] = iat
    token["hmac"] = hmac.new(SECRET, canonical_claims_digest(token).encode(), hashlib.sha256).hexdigest()
    gate = LeaseGate(SECRET, deployment_domain=DEPLOYMENT, lease_policy=_Posture(max_staleness_seconds=60))
    gate.update(token)
    with pytest.raises(CredentialError, match="issued-at"):
        await gate.check()
    assert not gate._consumed


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


async def test_policy_version_mismatch_refused_immediately():
    """A lease minted under an unadopted operator policy is structurally
    invalid: the gate refuses with LeaseRefusedError (immediate, no renewal
    wait) and the nonce stays unconsumed."""

    now = {"t": datetime.now(UTC)}
    gate = LeaseGate(
        SECRET,
        deployment_domain=DEPLOYMENT,
        clock=lambda: now["t"],
        lease_policy=_Posture(expected_policy_version="mp-2026.10"),
    )
    gate.update(_lease(policy_version="mp-2026.09", scope=["domain_a"]))
    with pytest.raises(LeaseRefusedError, match="policy version"):
        await gate.check()
    # The refused lease was not burned: adopting the version admits it.
    gate2 = LeaseGate(
        SECRET,
        deployment_domain=DEPLOYMENT,
        clock=lambda: now["t"],
        lease_policy=_Posture(expected_policy_version="mp-2026.10"),
    )
    gate2.update(_lease(policy_version="mp-2026.10", scope=["domain_a"]))
    claims = await gate2.check()
    assert claims.policy_version == "mp-2026.10"


async def test_legacy_lease_without_version_refused_under_adopted_policy():
    now = {"t": datetime.now(UTC)}
    gate = LeaseGate(
        SECRET,
        deployment_domain=DEPLOYMENT,
        clock=lambda: now["t"],
        lease_policy=_Posture(expected_policy_version="mp-2026.10"),
    )
    gate.update(_lease(scope=["domain_a"]))  # no policy_version claim
    with pytest.raises(LeaseRefusedError, match="'none'"):
        await gate.check()


async def test_no_policy_config_keeps_step6_posture():
    """Without a lease_policy block the gate validates expiry only — the
    step6 semantics the managed assembly opt into explicitly."""

    now = {"t": datetime.now(UTC)}
    gate = LeaseGate(SECRET, deployment_domain=DEPLOYMENT, clock=lambda: now["t"])
    gate.update(_lease(scope=["domain_a"]))
    claims = await gate.check()
    assert claims.policy_version is None


async def test_staleness_window_refuses_before_expiry():
    """Offline validity = min(exp, iat + staleness): the lease is not yet
    expired but the staleness deadline passed — refused, nonce unburned."""

    moment = datetime(2026, 10, 13, 12, 0, 0, tzinfo=UTC)
    lease = lease_claims(
        SECRET,
        iss=ISSUER,
        deployment_domain=DEPLOYMENT,
        sub="host-1",
        ttl_seconds=3600,
        scope=["domain_a"],
        now=moment,
    )[0]
    mono = {"t": 1000.0}
    # The anchor is stamped at install; trusted time judges the window.
    clock = TrustedClock(now_fn=lambda: moment, monotonic_fn=lambda: mono["t"])
    gate = LeaseGate(
        SECRET,
        deployment_domain=DEPLOYMENT,
        clock=lambda: moment,
        lease_policy=_Posture(expected_policy_version=None, max_staleness_seconds=60.0),
        trusted_clock=clock,
    )
    gate.update(lease)
    # 30s in: inside the window.
    clock_now = {"t": moment + timedelta(seconds=30)}
    gate._trusted_clock = TrustedClock(now_fn=lambda: clock_now["t"], monotonic_fn=lambda: mono["t"] + 30.0)
    claims = await gate.check()
    assert claims.iat is not None


async def test_staleness_window_exhausted_refuses():
    moment = datetime(2026, 10, 13, 12, 0, 0, tzinfo=UTC)
    lease = lease_claims(
        SECRET,
        iss=ISSUER,
        deployment_domain=DEPLOYMENT,
        sub="host-1",
        ttl_seconds=3600,
        scope=["domain_a"],
        now=moment,
    )[0]
    gate = LeaseGate(
        SECRET,
        deployment_domain=DEPLOYMENT,
        clock=lambda: moment,
        lease_policy=_Posture(expected_policy_version=None, max_staleness_seconds=60.0),
        trusted_clock=TrustedClock(now_fn=lambda: moment + timedelta(seconds=90), monotonic_fn=lambda: 1090.0),
    )
    gate.update(lease)
    with pytest.raises(CredentialError, match="window exhausted"):
        await gate.check()


async def test_untrusted_time_refuses_window_judgment():
    """A rolled-back wall clock must not extend the managed window: the
    gate refuses the deadline judgment instead of trusting the clock."""

    moment = datetime(2026, 10, 13, 12, 0, 0, tzinfo=UTC)
    lease = lease_claims(
        SECRET,
        iss=ISSUER,
        deployment_domain=DEPLOYMENT,
        sub="host-1",
        ttl_seconds=3600,
        scope=["domain_a"],
        policy_version="mp-2026.10",
        now=moment,
    )[0]
    # The anchor is stamped at install; the wall clock then rolls back
    # beyond tolerance — the gate refuses the deadline judgment instead of
    # trusting a clock that moved backwards relative to its own anchor.
    wall = {"t": moment}
    clock = TrustedClock(now_fn=lambda: wall["t"], monotonic_fn=lambda: 1000.0)
    gate = LeaseGate(
        SECRET,
        deployment_domain=DEPLOYMENT,
        clock=lambda: wall["t"],
        lease_policy=_Posture(expected_policy_version="mp-2026.10", max_staleness_seconds=3600.0),
        trusted_clock=clock,
    )
    gate.update(lease)
    wall["t"] = moment - timedelta(hours=2)
    with pytest.raises(CredentialError, match="time cannot be trusted"):
        await gate.check()


async def test_new_install_rebuilds_the_trusted_anchor():
    """A restart empties the gate; the next successful pull installs a new
    lease and re-anchors trusted time — the profile never judges a window
    against an anchor it did not observe."""

    moment = datetime(2026, 10, 13, 12, 0, 0, tzinfo=UTC)
    gate = LeaseGate(
        SECRET,
        deployment_domain=DEPLOYMENT,
        clock=lambda: moment,
        lease_policy=_Posture(expected_policy_version="mp-2026.10", max_staleness_seconds=60.0),
    )
    assert gate.anchor() is None and gate.facts() is None
    gate.update(_lease(policy_version="mp-2026.10", scope=["domain_a"]))
    anchor = gate.anchor()
    assert isinstance(anchor, TimeAnchor)
    facts = gate.facts()
    assert facts is not None and facts.policy_version == "mp-2026.10"
    # A legacy (never-verified) install clears both: no anchor, no facts.
    # The invalid lease is minted relative to the gate's frozen clock so it
    # genuinely fails verification there.
    invalid = lease_claims(
        SECRET,
        iss=ISSUER,
        deployment_domain=DEPLOYMENT,
        sub="host-1",
        ttl_seconds=-10,
        scope=["domain_a"],
        policy_version="mp-2026.10",
        now=moment,
    )[0]
    gate.update(invalid)
    assert gate.anchor() is None and gate.facts() is None
