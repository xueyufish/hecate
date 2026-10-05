"""Forwarding shim: implementation moved to ``hecate_durable`` (managed slice).

The credential primitives are pure stdlib and shared by the platform and
the independently installable runner — the same contract-cluster rule as
``hecate_durable.contracts``. Original import paths keep working.
"""

from __future__ import annotations

from hecate_durable.contracts.credentials import (  # noqa: F401
    CLAIMS_VERSION,
    MANAGED_CHANNEL_AUDIENCE,
    Claims,
    CredentialError,
    canonical_claims_digest,
    issue_credential,
    lease_claims,
    material_digest,
    new_nonce,
    solve_challenge,
    verify_challenge,
    verify_credential,
    verify_lease,
)
