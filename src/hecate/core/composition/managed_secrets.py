"""Operator-provisioned managed-channel secrets (issuer domain -> material).

The platform stores only SHA-256 digests of the signing material (in
``trust_roots``); the raw secrets live with the operator process and are
registered here at composition time — the same provision-out-of-band rule
as the plan's "凭据只以引用传递". API routes resolve credentials through
:func:`get_managed_secrets`; tests populate the registry directly.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_secrets: dict[str, bytes] = {}


def register_managed_secret(issuer_domain: str, secret: bytes) -> None:
    """Provision (or rotate) the raw material for one issuer domain."""

    with _lock:
        _secrets[issuer_domain] = secret


def clear_managed_secrets() -> None:
    """Test seam: drop every provisioned secret."""

    with _lock:
        _secrets.clear()


def get_managed_secrets() -> dict[str, bytes]:
    """The current issuer->secret map (channel credential verification)."""

    with _lock:
        return dict(_secrets)
