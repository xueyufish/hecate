"""Runtime re-verification of the local artifact trust policy.

Startup admission (``--artifact-trust``) is a one-time decision: once the
runner serves, the operator has no way to stop a new model call or tool
dispatch without restarting the host. This module closes that gap with an
explicit opt-in: the host freezes the admitted artifact facts at startup
and, before every NEW model request and real tool dispatch, re-reads the
operator's local trust policy file and re-checks them against it.

Fixed boundaries:

- Explicit revocations stay disabled for the lifetime of the process —
  clearing a revocation cannot resurrect trust without a restart and a
  fresh admission. An unusable policy refuses the current call; it is not
  evidence that the admitted identity was explicitly revoked.
- Only calls that have not been issued are constrained; nothing about
  in-flight requests is claimed.
- Queries, cancellation and the admin shutdown stay available, and the
  host version lock is not released on revocation (switching is still
  only possible while stopped).
- Already-completed actions and their receipts are never rewritten; a
  resumed run re-checks before any new call.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .artifact_signing import ArtifactAdmissionError, ArtifactTrustPolicy, VerifiedArtifact, load_trust_policy

# Reuses the engine's authorization vocabulary: a revocation refusal
# converges like any other policy denial, never as an acceptance success.
REVOCATION_STATUS = "authorization"
REVOCATION_CATEGORY = "policy_denied"


class RuntimeTrustRevokedError(Exception):
    """A pre-call trust re-check refused; the call must not be issued."""

    def __init__(self, refusal: RevocationRefusal) -> None:
        super().__init__(refusal.reason)
        self.refusal = refusal


@dataclass(frozen=True)
class RevocationRefusal:
    """The recorded facts of one refused call (no secret material)."""

    reason: str
    publisher_ref: str
    key_id: str
    artifact_sha256: str
    checked_path: str

    def evidence(self) -> dict:
        """Evidence payload correlating the refusal to the artifact."""

        return {
            "reason": self.reason,
            "publisher_ref": self.publisher_ref,
            "key_id": self.key_id,
            "artifact_sha256": self.artifact_sha256,
            "trust_policy": self.checked_path,
            "error_category": REVOCATION_CATEGORY,
        }


def _public_pem(key: Ed25519PublicKey) -> str:
    return key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")


class RuntimeTrustGuard:
    """Re-verify the frozen admission facts against the live policy file.

    The guard freezes the startup-admitted artifact identity (manifest
    digest, publisher, key id, the key's public bytes) from the SAME policy
    object admission used — never by re-reading the file, which an
    operator edit could race. Before each new model request or real tool
    dispatch it re-reads the policy from disk; no authorization decision is
    cached. Explicit identity revocations are sticky for the process.
    """

    def __init__(self, admission: VerifiedArtifact, startup_policy: ArtifactTrustPolicy) -> None:
        trust = startup_policy.publishers.get(admission.publisher_ref)
        key = trust.keys.get(admission.key_id) if trust is not None else None
        if key is None:
            # Admission verified against this policy, so the key must be
            # present; anything else means the caller passed a mismatched
            # policy object and the guard would silently check nothing.
            raise RuntimeError("runtime trust guard requires the policy object admission verified against")
        self._policy_path = startup_policy.path.absolute()
        self._publisher = admission.publisher_ref
        self._key_id = admission.key_id
        self._manifest_sha256 = admission.manifest_sha256
        self._public_pem = _public_pem(key)
        self._sticky_reason: str | None = None
        self._lock = threading.Lock()

    @property
    def policy_path(self) -> Path:
        """The fixed operator-supplied path consulted at each call boundary."""

        return self._policy_path

    def frozen_facts(self) -> dict:
        """The admission facts this guard holds (public key by digest only)."""

        return {
            "publisher_ref": self._publisher,
            "key_id": self._key_id,
            "manifest_sha256": self._manifest_sha256,
            "public_key_sha256": hashlib.sha256(self._public_pem.encode("utf-8")).hexdigest(),
        }

    def _refusal(self, reason: str) -> RevocationRefusal:
        return RevocationRefusal(
            reason=reason,
            publisher_ref=self._publisher,
            key_id=self._key_id,
            artifact_sha256=self._manifest_sha256,
            checked_path=str(self._policy_path),
        )

    def _stick(self, reason: str) -> RevocationRefusal:
        with self._lock:
            if self._sticky_reason is None:
                self._sticky_reason = reason
            return self._refusal(self._sticky_reason)

    def check(self) -> RevocationRefusal | None:
        """Re-read the policy and re-check the frozen facts before a call.

        Returns a :class:`RevocationRefusal` when the call must not be
        issued, ``None`` when the live policy still backs the frozen
        identity. Explicit identity revocations are sticky; an unusable
        policy refuses this call without inventing a permanent revocation.
        """

        with self._lock:
            if self._sticky_reason is not None:
                return self._refusal(self._sticky_reason)
        try:
            policy = load_trust_policy(self._policy_path)
        except ArtifactAdmissionError:
            # Fail closed on a missing, unreadable or malformed policy: the
            # call is refused rather than falling back to cached trust.
            # Parser diagnostics can quote malformed input, including secrets
            # accidentally pasted into a policy field. Keep audit reasons fixed.
            return self._refusal("artifact trust policy unusable; current trust cannot be verified")
        trust = policy.publishers.get(self._publisher)
        if trust is None:
            return self._stick(f"publisher {self._publisher!r} is no longer in the artifact trust policy")
        if trust.revoked:
            return self._stick(f"publisher {self._publisher!r} is revoked in the artifact trust policy")
        key = trust.keys.get(self._key_id)
        if key is None:
            return self._stick(f"signing key {self._key_id!r} is no longer trusted for publisher {self._publisher!r}")
        if self._key_id in trust.revoked_key_ids:
            return self._stick(f"signing key {self._key_id!r} is revoked for publisher {self._publisher!r}")
        if _public_pem(key) != self._public_pem:
            return self._stick(f"signing key {self._key_id!r} was replaced with a different public key")
        # Another caller may have observed a revocation while this caller
        # loaded an older, still-valid file snapshot. It cannot clear that fact.
        with self._lock:
            return self._refusal(self._sticky_reason) if self._sticky_reason is not None else None

    def require(self) -> None:
        """Raise :class:`RuntimeTrustRevokedError` when the call must not be issued."""

        refusal = self.check()
        if refusal is not None:
            raise RuntimeTrustRevokedError(refusal)
