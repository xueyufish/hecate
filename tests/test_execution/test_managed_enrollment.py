"""Managed runner enrollment tests: trust roots, credentials, resolver.

Covers task group 1 of managed-runner-enrollment: the HMAC credential
primitives (challenge, channel credential, lease with replay binding),
the workspace trust-root registry (registration, resolution, revocation,
fingerprint refresh), and the enrollment resolver wiring into
``set_managed_opt_in``'s admission chain.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from hecate.execution import managed_credentials as mc
from hecate.execution.enrollment_resolver import EnrollmentResolutionError, HmacEnrollmentResolver
from hecate.execution.task_run_registry import TaskRunRegistry
from hecate.execution.trust_roots import TrustRootNotFoundError, TrustRootRegistry
from hecate.models.organization import OrganizationModel
from hecate.models.user import UserModel
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole

WS = uuid.uuid4()
ORG = uuid.uuid4()
ADMIN = uuid.uuid4()
NON_ADMIN = uuid.uuid4()
ROOT_NAME = "host-root-alpha"
ISSUER = "managed-workspace-alpha"
SECRET = b"provisioned-hmac-secret-alpha"


# --- credential primitives ----------------------------------------------------


def test_credential_roundtrip_and_expiry():
    token, nonce = mc.issue_credential(
        SECRET, iss=ISSUER, sub="host-alpha", ttl_seconds=60, tenant=str(WS), now=datetime(2026, 1, 1, tzinfo=UTC)
    )
    assert nonce == token["nonce"]
    claims = mc.verify_credential(token, SECRET, now=datetime(2026, 1, 1, 0, 0, 30, tzinfo=UTC))
    assert claims.iss == ISSUER and claims.sub == "host-alpha" and claims.nonce == nonce
    with pytest.raises(mc.CredentialError, match="expired"):
        mc.verify_credential(token, SECRET, now=datetime(2026, 1, 1, 0, 2, tzinfo=UTC))


def test_credential_rejects_wrong_secret_bad_audience_and_tampering():
    token, _ = mc.issue_credential(SECRET, iss=ISSUER, sub="host", ttl_seconds=60)
    with pytest.raises(mc.CredentialError, match="signature"):
        mc.verify_credential(token, b"other-secret")
    tampered_aud = {**token, "aud": "somewhere-else"}
    with pytest.raises(mc.CredentialError):
        mc.verify_credential(tampered_aud, SECRET)
    # Client-asserted extras are unsigned noise: adding a role claim breaks
    # the signature (it changes the body) — the security property is that
    # unsigned extras can never grant anything.
    with pytest.raises(mc.CredentialError, match="signature"):
        mc.verify_credential({**token, "role": "admin"}, SECRET)


def test_challenge_flow():
    nonce = mc.new_nonce()
    answer = mc.solve_challenge(SECRET, nonce)
    assert mc.verify_challenge(SECRET, nonce, answer) is True
    assert mc.verify_challenge(SECRET, nonce, "wrong") is False


def test_lease_binds_deployment_domain_and_expiry():
    lease, nonce = mc.lease_claims(
        SECRET,
        iss=ISSUER,
        deployment_domain="host-alpha",
        sub="host-alpha",
        ttl_seconds=30,
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert nonce == lease["nonce"]
    claims = mc.verify_lease(
        lease, SECRET, deployment_domain="host-alpha", now=datetime(2026, 1, 1, 0, 0, 20, tzinfo=UTC)
    )
    assert claims.nonce == nonce
    # Another host's gate must not accept it (audience binding) — checked
    # within the validity window so the binding error, not expiry, fires.
    with pytest.raises(mc.CredentialError, match="bind"):
        mc.verify_lease(lease, SECRET, deployment_domain="host-beta", now=datetime(2026, 1, 1, 0, 0, 20, tzinfo=UTC))
    with pytest.raises(mc.CredentialError, match="expired"):
        mc.verify_lease(lease, SECRET, deployment_domain="host-alpha", now=datetime(2026, 1, 1, 0, 1, tzinfo=UTC))


def test_lease_carries_policy_version_and_iat_when_issued():
    """Managed lease profile: the operator policy version and signing
    moment are signed into the lease so the host can reject leases minted
    under an unadopted policy and derive staleness windows from ``iat``."""

    moment = datetime(2026, 1, 1, tzinfo=UTC)
    lease, _ = mc.lease_claims(
        SECRET,
        iss=ISSUER,
        deployment_domain="host-alpha",
        sub="host-alpha",
        ttl_seconds=30,
        policy_version="mp-2026.10",
        now=moment,
    )
    assert lease["policy_version"] == "mp-2026.10"
    assert lease["iat"] == moment.isoformat()
    claims = mc.verify_lease(lease, SECRET, deployment_domain="host-alpha", now=moment)
    assert claims.policy_version == "mp-2026.10"
    assert claims.iat == moment.isoformat()
    # Tampering with the policy version breaks the signature: the version
    # is part of the signed body, not advisory metadata.
    with pytest.raises(mc.CredentialError, match="signature"):
        mc.verify_lease({**lease, "policy_version": "mp-2026.11"}, SECRET, deployment_domain="host-alpha", now=moment)


def test_claims_without_policy_fields_stay_verifiable():
    """Leases and credentials minted before the managed lease profile
    carried no ``policy_version``/``iat``; they must still verify, and the
    new fields round-trip as None so gates can apply their own posture."""

    # Hand-built pre-profile lease body (what the old lease_claims emitted):
    # no policy_version, no iat, signature over exactly that body.
    moment = datetime(2026, 1, 1, tzinfo=UTC)
    body = {
        "contract_version": mc.CLAIMS_VERSION,
        "iss": ISSUER,
        "aud": "host-alpha",
        "sub": "host-alpha",
        "exp": (moment + timedelta(seconds=30)).isoformat(),
        "nonce": mc.new_nonce(),
    }
    signature = hmac.new(SECRET, mc.canonical_claims_digest(body).encode(), hashlib.sha256).hexdigest()
    legacy_lease = {**body, "hmac": signature}
    claims = mc.verify_lease(legacy_lease, SECRET, deployment_domain="host-alpha", now=moment)
    assert claims.policy_version is None and claims.iat is None
    assert claims.nonce == body["nonce"]
    # Omission (not a null marker) is the wire format: dropping the fields
    # and re-serializing reproduces the signed body exactly.
    rebuilt = mc.Claims.from_dict(body)
    assert rebuilt.to_dict() == body


# --- fixtures -------------------------------------------------------------------


@pytest.fixture
async def ws_env(db_session):
    db_session.add(OrganizationModel(id=ORG, name="org", slug=f"org-{ORG.hex[:12]}", owner_id=ADMIN))
    db_session.add(WorkspaceModel(id=WS, org_id=ORG, name="ws", slug=f"ws-{WS.hex}"))
    db_session.add(UserModel(id=ADMIN, email="admin@example.com", hashed_password=uuid.uuid4().hex))
    db_session.add(UserModel(id=NON_ADMIN, email="member@example.com", hashed_password=uuid.uuid4().hex))
    # Admission requires an ACTIVE WORKSPACE ADMINISTRATOR membership —
    # the role lives on the membership row, not on the user.
    db_session.add(WorkspaceMemberModel(workspace_id=WS, user_id=ADMIN, role=WorkspaceRole.ADMIN))
    db_session.add(WorkspaceMemberModel(workspace_id=WS, user_id=NON_ADMIN, role=WorkspaceRole.VIEWER))
    await db_session.commit()
    return db_session


@pytest.fixture
def registry(ws_env) -> TrustRootRegistry:
    return TrustRootRegistry(ws_env)


# --- trust root registry ----------------------------------------------------------


async def test_register_resolve_revoke(registry: TrustRootRegistry):
    digest = mc.material_digest(SECRET)
    row = await registry.register(
        workspace_id=WS,
        name=ROOT_NAME,
        material_digest=digest,
        issuer_domain=ISSUER,
        config_fingerprint="cfg-1",
    )
    assert row.revoked is False
    resolved = await registry.resolve(WS, ROOT_NAME)
    assert resolved.id == row.id

    await registry.revoke(WS, ROOT_NAME)
    with pytest.raises(TrustRootNotFoundError):
        await registry.resolve(WS, ROOT_NAME)
    # Peek still reads state (reconnect re-verification).
    peeked = await registry.peek(WS, ROOT_NAME)
    assert peeked is not None and peeked.revoked is True


async def test_register_rotation_preserves_revocation(registry: TrustRootRegistry):
    await registry.register(
        workspace_id=WS,
        name=ROOT_NAME,
        material_digest=mc.material_digest(SECRET),
        issuer_domain=ISSUER,
        config_fingerprint="cfg-1",
    )
    await registry.revoke(WS, ROOT_NAME)
    await registry.register(
        workspace_id=WS,
        name=ROOT_NAME,
        material_digest=mc.material_digest(b"rotated"),
        issuer_domain=ISSUER,
        config_fingerprint="cfg-2",
    )
    row = await registry.peek(WS, ROOT_NAME)
    assert row.revoked is True  # rotation is not un-revocation
    assert row.config_fingerprint == "cfg-2"


async def test_register_validation(registry: TrustRootRegistry):
    with pytest.raises(TrustRootNotFoundError.__mro__[1], match="name"):
        await registry.register(
            workspace_id=WS, name="", material_digest="0" * 64, issuer_domain=ISSUER, config_fingerprint="c"
        )
    with pytest.raises(TrustRootNotFoundError.__mro__[1], match="digest"):
        await registry.register(
            workspace_id=WS, name="x", material_digest="short", issuer_domain=ISSUER, config_fingerprint="c"
        )


async def test_secret_for_matches_digest(registry: TrustRootRegistry):
    await registry.register(
        workspace_id=WS,
        name=ROOT_NAME,
        material_digest=mc.material_digest(SECRET),
        issuer_domain=ISSUER,
        config_fingerprint="cfg-1",
    )
    row = await registry.resolve(WS, ROOT_NAME)
    assert await registry.secret_for(row, {ROOT_NAME: SECRET}) == SECRET
    from hecate.execution.trust_roots import TrustRootError

    with pytest.raises(TrustRootError, match="does not match"):
        await registry.secret_for(row, {ROOT_NAME: b"wrong"})


# --- resolver + admission chain ---------------------------------------------------


def _enrollment_refs() -> tuple[dict, dict, dict]:
    challenge_nonce = mc.new_nonce()
    refs = (
        {"issuer_domain": ISSUER, "id": "host-alpha", "name": ROOT_NAME},
        {"issuer_domain": ISSUER, "id": "root-alpha", "name": ROOT_NAME},
        {
            "contracts": ["0.1"],
            "capabilities": {"managed_channel": "pull"},
            "challenge": {
                "nonce": challenge_nonce,
                "answer": mc.solve_challenge(SECRET, challenge_nonce),
            },
        },
    )
    return refs


async def test_resolver_admits_with_trusted_state(ws_env, registry: TrustRootRegistry):
    host_ref, root_ref, versions = _enrollment_refs()
    await registry.register(
        workspace_id=WS,
        name=ROOT_NAME,
        material_digest=mc.material_digest(SECRET),
        issuer_domain=ISSUER,
        config_fingerprint="cfg-1",
    )
    resolver = HmacEnrollmentResolver(ws_env, secret_candidates={ROOT_NAME: SECRET})
    assert await resolver.verify(WS, host_ref, root_ref, versions) is True

    import traceback as _tb

    async def _tracing_resolver(workspace_id, host_ref, root_ref, versions):
        try:
            return await resolver.verify(workspace_id, host_ref, root_ref, versions)
        except BaseException:
            _tb.print_exc()
            raise

    registry_svc = TaskRunRegistry(ws_env, enrollment_resolver=resolver.verify)
    enrollment = await registry_svc.register_standalone_enrollment(
        workspace_id=WS, host_identity_ref=host_ref, trust_root_ref=root_ref, installed_versions=versions
    )
    admitted = await registry_svc.set_managed_opt_in(
        enrollment.id, WS, managed_new_runs=True, operator_id=ADMIN, admitted=True
    )
    assert admitted.admission.value == "admitted"
    assert admitted.managed_new_runs is True


async def test_resolver_blocks_untrusted_state(ws_env, registry: TrustRootRegistry):
    host_ref, root_ref, versions = _enrollment_refs()
    # No trust root registered: resolution fails, admission stays pending.
    resolver = HmacEnrollmentResolver(ws_env, secret_candidates={ROOT_NAME: SECRET})
    with pytest.raises(EnrollmentResolutionError):
        await resolver.verify(WS, host_ref, root_ref, versions)

    registry_svc = TaskRunRegistry(ws_env, enrollment_resolver=resolver.verify)
    enrollment = await registry_svc.register_standalone_enrollment(
        workspace_id=WS, host_identity_ref=host_ref, trust_root_ref=root_ref, installed_versions=versions
    )
    # Rejection RAISES after writing the rejection audit — it never returns
    # a silently-pending record.
    from hecate.execution.task_run_registry import TaskRunValidationError

    with pytest.raises(TaskRunValidationError, match="trusted resolver"):
        await registry_svc.set_managed_opt_in(
            enrollment.id, WS, managed_new_runs=True, operator_id=ADMIN, admitted=True
        )
    refreshed = await ws_env.get(type(enrollment), enrollment.id)
    assert refreshed.admission.value == "pending"
    assert refreshed.managed_new_runs is False


async def test_resolver_blocks_wrong_challenge_answer(ws_env, registry: TrustRootRegistry):
    host_ref, root_ref, versions = _enrollment_refs()
    versions["challenge"]["answer"] = "forged"
    await registry.register(
        workspace_id=WS,
        name=ROOT_NAME,
        material_digest=mc.material_digest(SECRET),
        issuer_domain=ISSUER,
        config_fingerprint="cfg-1",
    )
    resolver = HmacEnrollmentResolver(ws_env, secret_candidates={ROOT_NAME: SECRET})
    with pytest.raises(EnrollmentResolutionError, match="challenge"):
        await resolver.verify(WS, host_ref, root_ref, versions)


async def test_opt_in_requires_workspace_admin(ws_env, registry: TrustRootRegistry):
    host_ref, root_ref, versions = _enrollment_refs()
    await registry.register(
        workspace_id=WS,
        name=ROOT_NAME,
        material_digest=mc.material_digest(SECRET),
        issuer_domain=ISSUER,
        config_fingerprint="cfg-1",
    )
    registry_svc = TaskRunRegistry(
        ws_env, enrollment_resolver=HmacEnrollmentResolver(ws_env, {ROOT_NAME: SECRET}).verify
    )
    enrollment = await registry_svc.register_standalone_enrollment(
        workspace_id=WS, host_identity_ref=host_ref, trust_root_ref=root_ref, installed_versions=versions
    )
    from hecate.execution.task_run_registry import TaskRunValidationError

    with pytest.raises(TaskRunValidationError, match="workspace administrator"):
        await registry_svc.set_managed_opt_in(
            enrollment.id, WS, managed_new_runs=True, operator_id=NON_ADMIN, admitted=True
        )


async def test_opt_in_stores_operator_lease_policy(ws_env, registry: TrustRootRegistry):
    """The managed lease posture is an operator-controlled enrollment field:
    it persists through the opt-in door and defaults to {} (deny-by-default
    — a posture-less enrollment issues version-less leases)."""

    host_ref, root_ref, versions = _enrollment_refs()
    await registry.register(
        workspace_id=WS,
        name=ROOT_NAME,
        material_digest=mc.material_digest(SECRET),
        issuer_domain=ISSUER,
        config_fingerprint="cfg-1",
    )
    registry_svc = TaskRunRegistry(
        ws_env, enrollment_resolver=HmacEnrollmentResolver(ws_env, {ROOT_NAME: SECRET}).verify
    )
    enrollment = await registry_svc.register_standalone_enrollment(
        workspace_id=WS, host_identity_ref=host_ref, trust_root_ref=root_ref, installed_versions=versions
    )
    assert enrollment.lease_policy == {}

    admitted = await registry_svc.set_managed_opt_in(
        enrollment.id,
        WS,
        managed_new_runs=True,
        operator_id=ADMIN,
        admitted=True,
        managed_scope=["inventory"],
        lease_policy={"policy_version": "mp-2026.10", "max_staleness_seconds": 120.0},
    )
    assert admitted.lease_policy == {"policy_version": "mp-2026.10", "max_staleness_seconds": 120.0}
    refreshed = await ws_env.get(type(enrollment), enrollment.id)
    assert refreshed.lease_policy["policy_version"] == "mp-2026.10"


async def test_opt_in_rejects_invalid_lease_policy(ws_env, registry: TrustRootRegistry):
    """Unknown fields and malformed values are rejected at the same door —
    strict admission, no silent normalization of operator posture."""

    from hecate.execution.task_run_registry import TaskRunValidationError

    host_ref, root_ref, versions = _enrollment_refs()
    await registry.register(
        workspace_id=WS,
        name=ROOT_NAME,
        material_digest=mc.material_digest(SECRET),
        issuer_domain=ISSUER,
        config_fingerprint="cfg-1",
    )
    registry_svc = TaskRunRegistry(
        ws_env, enrollment_resolver=HmacEnrollmentResolver(ws_env, {ROOT_NAME: SECRET}).verify
    )
    enrollment = await registry_svc.register_standalone_enrollment(
        workspace_id=WS, host_identity_ref=host_ref, trust_root_ref=root_ref, installed_versions=versions
    )
    for bad in (
        {"unknown_field": "x"},
        {"policy_version": "  "},
        {"max_staleness_seconds": 0},
        {"max_staleness_seconds": "120"},
        ["not", "an", "object"],
    ):
        with pytest.raises(TaskRunValidationError, match="lease policy"):
            await registry_svc.set_managed_opt_in(
                enrollment.id, WS, managed_new_runs=True, operator_id=ADMIN, admitted=True, lease_policy=bad
            )


def test_issued_lease_carries_enrollment_policy_version():
    """issue_lease_for signs the enrollment posture's policy version into
    the lease; a posture-less enrollment issues a version-less lease (the
    host's managed profile refuses protected actions for it)."""

    from hecate.execution.managed_channel import HostContext, issue_lease_for

    moment = datetime(2026, 10, 13, tzinfo=UTC)
    claims = mc.Claims(
        iss=ISSUER,
        aud=mc.MANAGED_CHANNEL_AUDIENCE,
        sub="host-alpha",
        exp=(moment.replace(year=2027)).isoformat(),
        nonce=mc.new_nonce(),
        tenant=str(WS),
    )
    host = HostContext(
        workspace_id=WS,
        enrollment_id=uuid.uuid4(),
        deployment_domain="host-alpha",
        host_name="host-alpha",
        claims=claims,
        managed_scope=["inventory"],
        lease_policy={"policy_version": "mp-2026.10"},
    )
    lease = issue_lease_for(host, SECRET, ttl_seconds=120)
    verified = mc.verify_lease(lease, SECRET, deployment_domain="host-alpha", now=moment)
    assert verified.policy_version == "mp-2026.10"
    assert verified.scope == ["inventory"]

    postureless = HostContext(
        workspace_id=host.workspace_id,
        enrollment_id=host.enrollment_id,
        deployment_domain=host.deployment_domain,
        host_name=host.host_name,
        claims=claims,
        managed_scope=host.managed_scope,
        lease_policy={},
    )
    leaseless = issue_lease_for(postureless, SECRET, ttl_seconds=120)
    verifiedless = mc.verify_lease(leaseless, SECRET, deployment_domain="host-alpha", now=moment)
    assert verifiedless.policy_version is None
