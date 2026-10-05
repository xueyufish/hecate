"""Workspace trust-root registry - the single writer of ``trust_roots``.

One row is a workspace-scoped named trust material for managed runner
enrollment (step6/7). The registry is the enrollment resolver's backing
store: ``TaskRunRegistry.set_managed_opt_in``'s ``enrollment_resolver``
resolves an enrollment's named references through
:meth:`TrustRootRegistry.resolve` — both the host identity and the trust
root must name a non-revoked row in the same workspace, and the host must
prove material possession via the registration challenge.

Revocation is immediate for resolution: a revoked root blocks managed
opt-in and the reconnect re-verification chain. The expected-configuration
fingerprint is operator-refreshed; a host presenting a stale fingerprint
fails reconnect verification and stops receiving new deliveries (existing
local execution facts are unaffected).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.trust_root import TrustRootModel


class TrustRootError(Exception):
    """Trust-root registration or resolution failure (422/404 semantics)."""


class TrustRootNotFoundError(TrustRootError):
    """The named reference does not resolve in the workspace."""


class TrustRootRegistry:
    """Register / resolve / revoke workspace trust materials."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def register(
        self,
        *,
        workspace_id: uuid.UUID,
        name: str,
        material_digest: str,
        issuer_domain: str,
        config_fingerprint: str,
        meta: dict | None = None,
    ) -> TrustRootModel:
        """Provision one named trust material (operator action).

        The raw signing secret is provisioned to the host out of band; only
        its digest is stored. Re-registering an existing name replaces the
        material digest and config fingerprint (credential rotation path) —
        revocation state is untouched by rotation.
        """

        if not name or not name.strip():
            raise TrustRootError("trust root name must be a non-empty string")
        if not material_digest or len(material_digest) != 64:
            raise TrustRootError("material_digest must be a sha-256 hex digest")
        if not issuer_domain or not config_fingerprint:
            raise TrustRootError("issuer_domain and config_fingerprint are required")
        row = (
            await self._session.execute(
                select(TrustRootModel).where(
                    TrustRootModel.workspace_id == workspace_id,
                    TrustRootModel.name == name,
                    TrustRootModel.deleted.is_(False),
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = TrustRootModel(
                name=name,
                material_digest=material_digest,
                issuer_domain=issuer_domain,
                config_fingerprint=config_fingerprint,
                meta=dict(meta or {}),
                workspace_id=workspace_id,
            )
            self._session.add(row)
        else:
            row.material_digest = material_digest
            row.issuer_domain = issuer_domain
            row.config_fingerprint = config_fingerprint
            if meta is not None:
                row.meta = dict(meta)
        await self._session.flush()
        return row

    async def resolve(self, workspace_id: uuid.UUID, name: str) -> TrustRootModel:
        """Resolve one named reference to a non-revoked root, or raise."""

        row = await self._peek(workspace_id, name)
        if row is None or row.revoked:
            raise TrustRootNotFoundError(f"trust root {name!r} does not resolve in workspace")
        return row

    async def peek(self, workspace_id: uuid.UUID, name: str) -> TrustRootModel | None:
        """Resolve regardless of revocation (reconnect re-verification reads state)."""

        return await self._peek(workspace_id, name)

    async def revoke(self, workspace_id: uuid.UUID, name: str) -> TrustRootModel:
        row = await self._peek(workspace_id, name)
        if row is None:
            raise TrustRootNotFoundError(f"trust root {name!r} does not resolve in workspace")
        row.revoked = True
        await self._session.flush()
        return row

    async def refresh_config_fingerprint(self, workspace_id: uuid.UUID, name: str, fingerprint: str) -> TrustRootModel:
        """Operator-refreshed expected-configuration digest (reconnect gate)."""

        if not fingerprint:
            raise TrustRootError("config fingerprint must be a non-empty string")
        row = await self._peek(workspace_id, name)
        if row is None:
            raise TrustRootNotFoundError(f"trust root {name!r} does not resolve in workspace")
        row.config_fingerprint = fingerprint
        await self._session.flush()
        return row

    async def secret_for(self, row: TrustRootModel, secret_candidates: dict[str, bytes]) -> bytes:
        """Look up the raw secret for one registered root among provisioned candidates.

        The registry stores only digests; the operator-side process holds the
        raw material (keyed by root name). Raises when the candidate set does
        not match the registered digest — a provisioned-secret mismatch.
        """

        # Candidates may be keyed by root name or by issuer domain (both
        # identify the same material; composition provisioning picks one).
        secret = secret_candidates.get(row.name) or secret_candidates.get(row.issuer_domain)
        if secret is None:
            raise TrustRootError(f"no provisioned secret for trust root {row.name!r}")
        from hecate.execution.managed_credentials import material_digest

        if material_digest(secret) != row.material_digest:
            raise TrustRootError(f"provisioned secret for {row.name!r} does not match the registered digest")
        return secret

    async def _peek(self, workspace_id: uuid.UUID, name: str) -> TrustRootModel | None:
        return (
            await self._session.execute(
                select(TrustRootModel).where(
                    TrustRootModel.workspace_id == workspace_id,
                    TrustRootModel.name == name,
                    TrustRootModel.deleted.is_(False),
                )
            )
        ).scalar_one_or_none()
