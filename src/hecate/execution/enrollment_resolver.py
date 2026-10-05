"""Trusted enrollment resolver - wires step4's injection point to step6/7 reality.

``TaskRunRegistry.set_managed_opt_in`` requires an ``enrollment_resolver``
callable before it admits an enrollment and flips ``managed_new_runs``.
:class:`HmacEnrollmentResolver` is the preview-profile implementation
(design D6): both named references must resolve through
:class:`TrustRootRegistry` to non-revoked workspace roots, the host's
reported identity must name the host root, and the host must have proven
material possession of the signing secret via the registration challenge
(the answer is verified against the provisioned secret candidate).

Resolution failures raise — the registry converts them into "admission
remains pending" audit rows, never into silent admission.
"""

from __future__ import annotations

import uuid
from typing import Any

from hecate.execution.managed_credentials import verify_challenge
from hecate.execution.trust_roots import TrustRootNotFoundError, TrustRootRegistry


class EnrollmentResolutionError(Exception):
    """The enrollment's references do not resolve through trusted state."""


class HmacEnrollmentResolver:
    """Resolve enrollment references against workspace trust roots + challenge proof.

    ``secret_candidates`` maps trust-root name -> raw provisioned secret
    (operator-side material; the platform stores digests only). ``verify``
    is the callable shape ``TaskRunRegistry`` injects:
    ``(workspace_id, host_identity_ref, trust_root_ref, installed_versions) -> bool``.
    The host's challenge answer travels inside ``installed_versions`` under
    the ``challenge`` key (nonce + answer), recorded at registration time.
    """

    def __init__(self, session, secret_candidates: dict[str, bytes] | None = None) -> None:
        self._registry = TrustRootRegistry(session)
        self._secrets = secret_candidates or {}

    async def verify(
        self,
        workspace_id: uuid.UUID,
        host_identity_ref: dict[str, Any],
        trust_root_ref: dict[str, Any],
        installed_versions: dict[str, Any],
    ) -> bool:
        host_name = str(host_identity_ref.get("name") or "")
        root_name = str(trust_root_ref.get("name") or "")
        if not host_name or not root_name:
            raise EnrollmentResolutionError("named references must carry a name")

        host_root = await self._resolve(workspace_id, host_name)
        material_root = await self._resolve(workspace_id, root_name)

        if host_root.issuer_domain != material_root.issuer_domain:
            raise EnrollmentResolutionError("host identity and trust root issuer domains disagree")

        # The host's reported identity must name the host root itself —
        # an enrollment cannot vouch for a different host's material.
        if host_name != root_name and host_identity_ref.get("trust_root") not in (None, root_name):
            raise EnrollmentResolutionError("host identity does not bind the presented trust root")

        # Material possession: the registration challenge answer verifies
        # against the provisioned secret candidate for the root.
        challenge = installed_versions.get("challenge") or {}
        nonce = str(challenge.get("nonce") or "")
        answer = str(challenge.get("answer") or "")
        if not nonce or not answer:
            raise EnrollmentResolutionError("registration challenge proof missing")
        try:
            secret = await self._registry.secret_for(material_root, self._secrets)
        except Exception as exc:  # noqa: BLE001 — surfaced as pending, never silent admission
            raise EnrollmentResolutionError(str(exc)) from exc
        if not verify_challenge(secret, nonce, answer):
            raise EnrollmentResolutionError("registration challenge answer does not verify")
        return True

    async def _resolve(self, workspace_id: uuid.UUID, name: str):
        try:
            return await self._registry.resolve(workspace_id, name)
        except TrustRootNotFoundError as exc:
            raise EnrollmentResolutionError(str(exc)) from exc
