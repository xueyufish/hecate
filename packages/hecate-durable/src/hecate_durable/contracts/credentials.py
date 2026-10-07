"""HMAC credential primitives for the managed runner channel (step6/7).

Preview-profile credentials per the plan's managed slice: the platform and
each enrolled host share an HMAC signing secret (provisioned out of band;
the platform stores only its SHA-256 digest — see
``hecate.models.trust_root.TrustRootModel``). Three operations:

- **Registration challenge** — the platform issues a nonce; the host
  answers with ``HMAC(secret, nonce)``. A correct answer proves material
  possession without transmitting it.
- **Channel credential** — a signed bearer token carrying the
  ``security-claims`` fields (iss = the trust root's issuer domain,
  aud = the managed-channel audience, sub = host identity, exp, nonce);
  the signature is ``HMAC(secret, canonical(claims))``. Every managed
  API request presents one; the platform verifies signature, expiry,
  audience, and issuer — client-asserted roles or scopes are ignored.
- **Authorization lease** — the same token shape delivered inside pull
  responses (never a client request); the host's :class:`LeaseGate`
  verifies issuer/audience/deployment binding/expiry and consumes the
  nonce locally, so a replayed or expired lease cannot re-arm actions.

Nonce uniqueness: every credential and lease carries a unique nonce; the
platform tracks issued nonces and the host records consumed lease nonces
(a stale nonce is a replay and is rejected).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

MANAGED_CHANNEL_AUDIENCE = "hecate:managed-channel"

CLAIMS_VERSION = "0.1"


class CredentialError(Exception):
    """A presented credential or lease failed verification."""


def canonical_claims_digest(claims: dict[str, Any]) -> str:
    """SHA-256 over canonical JSON — the HMAC input for a token."""

    canonical = json.dumps(claims, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def material_digest(secret: bytes) -> str:
    """Server-side digest of a provisioned secret (raw secret never stored)."""

    return hashlib.sha256(secret).hexdigest()


def new_nonce() -> str:
    return f"n-{secrets.token_hex(16)}"


@dataclass(frozen=True)
class Claims:
    """The managed-channel claims set (security-claims semantics).

    ``scope`` is the lease-side data-domain allowlist (step6b): present
    only on authorization leases, it names the data domains the lease's
    holder may address with protected actions. The HMAC covers it because
    the signature digests whatever :meth:`to_dict` emits.
    """

    iss: str
    aud: str
    sub: str
    exp: str
    nonce: str
    tenant: str | None = None
    delegation_ref: str | None = None
    scope: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "contract_version": CLAIMS_VERSION,
            "iss": self.iss,
            "aud": self.aud,
            "sub": self.sub,
            "exp": self.exp,
            "nonce": self.nonce,
        }
        if self.tenant is not None:
            out["tenant"] = self.tenant
        if self.delegation_ref is not None:
            out["delegation_ref"] = self.delegation_ref
        if self.scope is not None:
            out["scope"] = list(self.scope)
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Claims:
        try:
            scope = data.get("scope")
            if scope is not None and not (isinstance(scope, list) and all(isinstance(d, str) for d in scope)):
                raise CredentialError("credential scope must be a list of data-domain strings")
            return cls(
                iss=str(data["iss"]),
                aud=str(data["aud"]),
                sub=str(data["sub"]),
                exp=str(data["exp"]),
                nonce=str(data["nonce"]),
                tenant=data.get("tenant"),
                delegation_ref=data.get("delegation_ref"),
                scope=list(scope) if scope is not None else None,
            )
        except KeyError as exc:
            raise CredentialError(f"credential claims missing field {exc}") from exc


def _epoch(value: str) -> datetime:
    try:
        deadline = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise CredentialError(f"unparsable expiry {value!r}") from exc
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    return deadline


def issue_credential(
    secret: bytes,
    *,
    iss: str,
    sub: str,
    ttl_seconds: float,
    tenant: str | None = None,
    now: datetime | None = None,
    aud: str = MANAGED_CHANNEL_AUDIENCE,
) -> tuple[dict[str, Any], str]:
    """Sign one channel credential; returns (token, nonce).

    The token is the claims dict plus an ``hmac`` field; verification
    recomputes the digest over the claims and compares digests.
    """

    moment = now or datetime.now(UTC)
    claims = Claims(
        iss=iss,
        aud=aud,
        sub=sub,
        exp=(moment + timedelta(seconds=ttl_seconds)).isoformat(),
        nonce=new_nonce(),
        tenant=tenant,
    )
    body = claims.to_dict()
    signature = hmac.new(secret, canonical_claims_digest(body).encode(), hashlib.sha256).hexdigest()
    return {**body, "hmac": signature}, claims.nonce


def verify_credential(token: dict[str, Any], secret: bytes, *, now: datetime | None = None) -> Claims:
    """Verify signature, expiry, and audience; returns the claims.

    Client-asserted extra fields (roles, scopes, self-reported identity)
    are not part of the signed claims and are ignored by design.
    """

    body = {k: v for k, v in token.items() if k != "hmac"}
    presented = token.get("hmac")
    if not isinstance(presented, str):
        raise CredentialError("credential missing signature")
    expected = hmac.new(secret, canonical_claims_digest(body).encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, presented):
        raise CredentialError("credential signature mismatch")
    claims = Claims.from_dict(body)
    moment = now or datetime.now(UTC)
    if _epoch(claims.exp) <= moment:
        raise CredentialError("credential expired")
    if claims.aud != MANAGED_CHANNEL_AUDIENCE:
        raise CredentialError(f"credential audience {claims.aud!r} is not the managed channel")
    return claims


def solve_challenge(secret: bytes, challenge_nonce: str) -> str:
    """Registration challenge answer: HMAC(secret, nonce)."""

    return hmac.new(secret, challenge_nonce.encode(), hashlib.sha256).hexdigest()


def verify_challenge(secret: bytes, challenge_nonce: str, answer: str) -> bool:
    expected = solve_challenge(secret, challenge_nonce)
    return hmac.compare_digest(expected, answer)


def lease_claims(
    secret: bytes,
    *,
    iss: str,
    deployment_domain: str,
    sub: str,
    ttl_seconds: float,
    scope: list[str] | None = None,
    tenant: str | None = None,
    now: datetime | None = None,
) -> tuple[dict[str, Any], str]:
    """Sign one authorization lease (aud = the deployment domain binding).

    The lease's audience is the host's deployment domain — a lease issued
    for one enrolled host does not verify against another's gate. ``scope``
    names the data domains protected actions may address; a lease without
    one authorizes nothing scope-checkable (the gate denies by default).
    """

    moment = now or datetime.now(UTC)
    claims = Claims(
        iss=iss,
        aud=deployment_domain,
        sub=sub,
        exp=(moment + timedelta(seconds=ttl_seconds)).isoformat(),
        nonce=new_nonce(),
        scope=list(scope) if scope is not None else None,
        tenant=tenant,
    )
    body = claims.to_dict()
    signature = hmac.new(secret, canonical_claims_digest(body).encode(), hashlib.sha256).hexdigest()
    return {**body, "hmac": signature}, claims.nonce


def verify_lease(
    token: dict[str, Any], secret: bytes, *, deployment_domain: str, now: datetime | None = None
) -> Claims:
    """Verify a lease for one host's deployment binding (gate-side)."""

    body = {k: v for k, v in token.items() if k != "hmac"}
    presented = token.get("hmac")
    if not isinstance(presented, str):
        raise CredentialError("lease missing signature")
    expected = hmac.new(secret, canonical_claims_digest(body).encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, presented):
        raise CredentialError("lease signature mismatch")
    claims = Claims.from_dict(body)
    moment = now or datetime.now(UTC)
    if _epoch(claims.exp) <= moment:
        raise CredentialError("lease expired")
    if claims.aud != deployment_domain:
        raise CredentialError(f"lease audience {claims.aud!r} does not bind deployment {deployment_domain!r}")
    return claims
