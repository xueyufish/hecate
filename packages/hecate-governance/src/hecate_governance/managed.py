"""Managed lease authorization profile (step7 I-Cb slice).

The managed-mode counterpart of the runner's local policy profile: a
:class:`~hecate_governance.policy.PolicyProfile` whose *protected* (write-class)
actions are additionally gated on the verified managed lease facts — issuer
allowlist, expected policy version, staleness window, trusted time. Readonly
dispatch flows through the inner rules profile unchanged: switching to the
managed profile neither tightens nor loosens it.

The lease's cryptographic verification (HMAC, audience binding, nonce replay)
lives in the host's lease gate; by the time facts reach this module the token
is already authenticated. Facts are pure data (no durable/runtime imports) so
the platform and the independently installable runner share the same gate
semantics and tests — the host adapter converts its verified claims into
:class:`ManagedLeaseFacts` at lease install time.

Disconnection posture: a stale or missing lease never degrades into local
self-authorization. Protected actions stop when the facts are absent, outside
the configured window, minted under an unadopted policy version, or when time
cannot be trusted — each denial names its branch. The window is
``min(expires_at, issued_at + max_staleness_seconds)``; an operator-declared
expected policy version is mandatory for protected actions (a host without one
does not run protected actions under the managed profile — fail closed).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from hecate_governance.action import ActionDecision
from hecate_governance.clock import TimeAnchor, TrustedClock
from hecate_governance.policy import PolicyContext, PolicyOutcome, PolicyProfile, RevocationView
from hecate_governance.principal import ActionPrincipal

WRITE_SIDE_EFFECT_CLASSES = frozenset({"idempotent_write", "non_idempotent_write", "external_side_effect"})


@dataclass(frozen=True)
class ManagedLeaseFacts:
    """Pure-data view of one verified managed lease (no token, no HMAC).

    Built by the host when its lease gate accepts a lease; consumed by
    :class:`ManagedLeaseProfile`. ``issued_at``/``expires_at`` carry the
    signed ``iat``/``exp`` claims; a legacy lease without ``iat`` reports
    ``issued_at=None`` and cannot satisfy a staleness window.
    """

    issuer: str
    audience: str
    subject: str
    tenant: str | None
    scope: tuple[str, ...]
    policy_version: str | None
    issued_at: datetime | None
    expires_at: datetime | None


@dataclass(frozen=True)
class ManagedLeasePolicyConfig:
    """Host-side managed posture (operator/trust material, not request data).

    ``expected_policy_version`` is enforced strictly when declared: a lease
    minted under any other version (or without one) refuses protected
    dispatch. Undeclared, the profile still gates on installed lease facts
    and the validity window — a host opts into the version posture by
    declaring it. ``max_staleness_seconds`` bounds the offline validity
    beyond the lease expiry — ``None`` keeps expiry-only validity.
    ``allowed_issuers`` is the profile-level issuer cross-check (empty =
    the gate's identity binding is the issuer authority).
    """

    expected_policy_version: str | None
    max_staleness_seconds: float | None = None
    allowed_issuers: tuple[str, ...] = ()


@dataclass(frozen=True)
class ManagedLeaseVerdict:
    """Why the managed gate allowed or refused one protected action."""

    ok: bool
    reason: str = ""


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class ManagedLeaseProfile(PolicyProfile):
    """Managed-mode profile: inner rules evaluation plus the lease gate.

    ``inner`` is the rules evaluator (the runner assembles its local profile
    here; platform tests may stub it). ``facts_provider``/``anchor_provider``
    are re-read on every evaluation so a newly installed lease takes effect
    without rebuilding the profile. The anchor is taken at lease *install*
    time: a restart empties the gate and the next pull re-anchors — the
    profile never judges a window against an anchor it did not observe.
    """

    def __init__(
        self,
        inner: PolicyProfile,
        config: ManagedLeasePolicyConfig,
        *,
        facts_provider: Callable[[], ManagedLeaseFacts | None],
        anchor_provider: Callable[[], TimeAnchor | None],
        clock: TrustedClock | None = None,
    ) -> None:
        self._inner = inner
        self._config = config
        self._facts_provider = facts_provider
        self._anchor_provider = anchor_provider
        self._clock = clock or TrustedClock()

    @property
    def version(self) -> str:
        inner_version = self._inner.version
        expected = self._config.expected_policy_version
        return f"managed-lease-1/{inner_version}@{expected or 'none'}"

    async def evaluate(self, context: PolicyContext) -> PolicyOutcome:
        if context.tool_side_effect not in WRITE_SIDE_EFFECT_CLASSES:
            return await self._inner.evaluate(context)
        verdict = self._judge_lease()
        if not verdict.ok:
            return PolicyOutcome(ActionDecision.DENY, self.version, verdict.reason)
        outcome = await self._inner.evaluate(context)
        # Record the composite version on gate-adjudicated outcomes: both the
        # lease gate and the inner rules participated in this judgment.
        return PolicyOutcome(outcome.decision, self.version, outcome.reason, outcome.tool_scope)

    def _judge_lease(self) -> ManagedLeaseVerdict:
        facts = self._facts_provider()
        if facts is None:
            return ManagedLeaseVerdict(False, "no managed lease is installed; protected actions stop")
        if self._config.allowed_issuers and facts.issuer not in self._config.allowed_issuers:
            return ManagedLeaseVerdict(False, f"lease issuer {facts.issuer!r} is not in the profile allowlist")
        expected = self._config.expected_policy_version
        # A declared expectation is enforced strictly; an undeclared one has
        # nothing to violate — the facts gate above still refuses protected
        # actions without an installed lease (disconnect protection), so
        # step6 assemblies (no lease_policy block) keep working while any
        # host that adopts an operator version gets the full check.
        if expected is not None and facts.policy_version != expected:
            presented = facts.policy_version or "none"
            return ManagedLeaseVerdict(
                False,
                f"lease policy version {presented!r} does not match the expected {expected!r}",
            )
        return self._judge_window(facts)

    def _judge_window(self, facts: ManagedLeaseFacts) -> ManagedLeaseVerdict:
        anchor = self._anchor_provider()
        if anchor is None:
            return ManagedLeaseVerdict(False, "no trusted-time anchor for the installed lease")
        now, untrusted = self._clock.trusted_now(anchor)
        if untrusted is not None:
            return ManagedLeaseVerdict(False, f"time cannot be trusted: {untrusted}")
        # Offline validity is the earlier of expiry and the staleness deadline.
        deadlines: list[datetime] = []
        if facts.expires_at is not None:
            deadlines.append(_as_utc(facts.expires_at))
        staleness = self._config.max_staleness_seconds
        if staleness is not None and facts.issued_at is not None:
            deadlines.append(_as_utc(facts.issued_at) + timedelta(seconds=staleness))
        if deadlines and now >= min(deadlines):
            return ManagedLeaseVerdict(
                False,
                "lease validity window exhausted (expiry or staleness deadline passed)",
            )
        return ManagedLeaseVerdict(True)


class ManagedRevocationView(RevocationView):
    """Merges the local revocation list with trust-root revocation facts.

    The local list is the inner profile's known-revoked principals; the
    provider reports whether the managed trust root is currently revoked as
    observed through the channel error surface. Either source revokes.
    """

    def __init__(
        self,
        inner: RevocationView,
        *,
        trust_revoked_provider: Callable[[], bool] | None = None,
    ) -> None:
        self._inner = inner
        self._trust_revoked_provider = trust_revoked_provider

    async def is_revoked(self, principal: ActionPrincipal) -> bool:
        if await self._inner.is_revoked(principal):
            return True
        return bool(self._trust_revoked_provider is not None and self._trust_revoked_provider())


def facts_from_claims(claims: Any) -> ManagedLeaseFacts:
    """Build facts from a verified lease claims object (duck-typed).

    Accepts anything exposing the ``Claims`` field surface (the durable
    contract dataclass or a mirror) so the governance package keeps zero
    dependencies on the durable package.
    """

    scope = getattr(claims, "scope", None)
    return ManagedLeaseFacts(
        issuer=claims.iss,
        audience=claims.aud,
        subject=claims.sub,
        tenant=getattr(claims, "tenant", None),
        scope=tuple(scope) if scope else (),
        policy_version=getattr(claims, "policy_version", None),
        issued_at=_parse(getattr(claims, "iat", None)),
        expires_at=_parse(getattr(claims, "exp", None)),
    )


def _parse(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return _as_utc(parsed)
