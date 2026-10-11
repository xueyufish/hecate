"""Verified identity envelope for controlled actions.

The envelope is the single identity shape consumed by policy evaluation,
approval binding and action ledgers on both the platform and the standalone
host. Every field is derived server-side from verified credentials (platform
entries), the runner's local trust material, or the managed delivery stamp.
Self-asserted request fields are structurally ignored: only the verified
subject is ever read from a claims mapping, and privileged attributes
(roles, domains, principal ids) must be passed explicitly by the deriving
adapter.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class PrincipalSource(StrEnum):
    """Where the verified identity came from."""

    PLATFORM_ENTRY = "platform_entry"
    RUNNER_LOCAL = "runner_local"
    MANAGED_DELIVERY = "managed_delivery"


class ActionPrincipal(BaseModel):
    """Immutable identity envelope for one controlled action.

    ``actor`` is the verified subject. ``agent_principal`` identifies the Agent
    principal when the action is performed by an agent, ``on_behalf_of`` and
    ``delegation_id`` carry the human/delegation chain. ``domains`` is the
    granted data-domain scope; ``deployment_id`` is the non-confusable
    deployment identifier used when no platform tenant exists.
    """

    model_config = ConfigDict(frozen=True)

    actor: str
    source: PrincipalSource
    agent_principal: str | None = None
    on_behalf_of: str | None = None
    delegation_id: str | None = None
    domains: tuple[str, ...] = Field(default=())
    deployment_id: str | None = None

    @classmethod
    def from_verified_claims(
        cls,
        claims: Mapping[str, object],
        *,
        source: PrincipalSource,
        domains: tuple[str, ...],
        deployment_id: str | None = None,
        agent_principal: str | None = None,
        on_behalf_of: str | None = None,
        delegation_id: str | None = None,
    ) -> ActionPrincipal:
        """Build the envelope from a server-verified claims mapping.

        Only ``sub`` (the verified subject) is read from ``claims``. Any other
        key — roles, domains, principal or delegation assertions — is ignored:
        those attributes are authorization decisions and must be derived
        server-side and passed explicitly.
        """
        actor = claims.get("sub")
        if not isinstance(actor, str) or not actor:
            raise ValueError("verified claims must contain a non-empty 'sub'")
        return cls(
            actor=actor,
            source=source,
            agent_principal=agent_principal,
            on_behalf_of=on_behalf_of,
            delegation_id=delegation_id,
            domains=tuple(domains),
            deployment_id=deployment_id,
        )

    def fingerprint(self) -> str:
        """Stable digest of the identity-bearing fields.

        Used by invocation-conflict detection: two envelopes with different
        fingerprints must never share one ``invocation_id``.
        """
        payload = {
            "actor": self.actor,
            "agent_principal": self.agent_principal,
            "on_behalf_of": self.on_behalf_of,
            "delegation_id": self.delegation_id,
            "domains": list(self.domains),
            "deployment_id": self.deployment_id,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
