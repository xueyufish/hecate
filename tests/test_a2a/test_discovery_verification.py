"""Tests for A2A Agent Card signature verification (p1-audit-cost-a2a).

Covers the a2a-protocol delta: fail-closed discovery when verification is
demanded — unsigned, tampered, and untrusted-issuer cards are rejected;
verified cards carry the ``verified`` marker; unverified fetches are
explicitly marked as such.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from hecate.channel.a2a.client.discovery import discover_agent_card
from hecate.channel.a2a.signing import generate_es256_keypair, sign_agent_card
from hecate.core.config import settings

BASE_URL = "https://remote.example.com"


def _card_data() -> dict[str, Any]:
    return {
        "name": "Remote Agent",
        "description": "A remote agent",
        "version": "1.0.0",
        "url": BASE_URL,
        "capabilities": {"streaming": True},
        "skills": [],
    }


def _trusted_jwks(kid: str = "trusted-kid") -> tuple[dict[str, Any], dict[str, Any]]:
    """Generate a keypair plus a JWKS document trusting its public key."""
    private_jwk, public_jwk = generate_es256_keypair()
    jwks = {"keys": [{**public_jwk, "kid": kid}]}
    return private_jwk, jwks


def _install_remote(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]) -> None:
    class _FakeResponse:
        def __init__(self, payload: dict[str, Any]) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return self._payload

    async def fake_get(self: httpx.AsyncClient, url: str, **kwargs: Any) -> _FakeResponse:
        return _FakeResponse(payload)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)


@pytest.fixture
def trusted_config(monkeypatch: pytest.MonkeyPatch, private_jwk: dict[str, Any]) -> None:
    jwks = {"keys": [{**private_jwk, "kid": "trusted-kid"}]}
    monkeypatch.setattr(settings, "A2A_TRUSTED_JWKS", json.dumps(jwks))
    monkeypatch.setattr(settings, "A2A_TRUSTED_JWKS_PATH", "")


@pytest.fixture
def private_jwk() -> dict[str, Any]:
    private, _public = generate_es256_keypair()
    return private


@pytest.mark.asyncio
async def test_unsigned_card_rejected_when_verification_demanded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_remote(monkeypatch, _card_data())
    with pytest.raises(ValueError, match="unsigned"):
        await discover_agent_card(BASE_URL, verify_signature=True)


@pytest.mark.asyncio
async def test_unconfigured_trust_root_fails_closed(
    monkeypatch: pytest.MonkeyPatch, private_jwk: dict[str, Any]
) -> None:
    card_data = sign_agent_card(_card_data(), private_jwk, kid="trusted-kid")
    _install_remote(monkeypatch, card_data)
    monkeypatch.setattr(settings, "A2A_TRUSTED_JWKS", "")
    monkeypatch.setattr(settings, "A2A_TRUSTED_JWKS_PATH", "")
    with pytest.raises(ValueError, match="no trusted JWKS"):
        await discover_agent_card(BASE_URL, verify_signature=True)


@pytest.mark.asyncio
async def test_tampered_card_rejected(
    monkeypatch: pytest.MonkeyPatch, trusted_config: None, private_jwk: dict[str, Any]
) -> None:
    card_data = sign_agent_card(_card_data(), private_jwk, kid="trusted-kid")
    card_data["description"] = "tampered after signing"
    _install_remote(monkeypatch, card_data)
    with pytest.raises(ValueError, match="verification failed"):
        await discover_agent_card(BASE_URL, verify_signature=True)


@pytest.mark.asyncio
async def test_unknown_issuer_rejected(monkeypatch: pytest.MonkeyPatch, trusted_config: None) -> None:
    rogue_key, _ = generate_es256_keypair()
    card_data = sign_agent_card(_card_data(), rogue_key, kid="rogue-kid")
    _install_remote(monkeypatch, card_data)
    with pytest.raises(ValueError, match="verification failed"):
        await discover_agent_card(BASE_URL, verify_signature=True)


@pytest.mark.asyncio
async def test_valid_signature_marks_card_verified(
    monkeypatch: pytest.MonkeyPatch, trusted_config: None, private_jwk: dict[str, Any]
) -> None:
    card_data = sign_agent_card(_card_data(), private_jwk, kid="trusted-kid")
    _install_remote(monkeypatch, card_data)
    card = await discover_agent_card(BASE_URL, verify_signature=True)
    assert card.verified is True
    assert card.name == "Remote Agent"


@pytest.mark.asyncio
async def test_unverified_fetch_is_explicitly_marked(
    monkeypatch: pytest.MonkeyPatch, private_jwk: dict[str, Any]
) -> None:
    card_data = sign_agent_card(_card_data(), private_jwk, kid="trusted-kid")
    _install_remote(monkeypatch, card_data)
    card = await discover_agent_card(BASE_URL, verify_signature=False)
    assert card.verified is False


@pytest.mark.asyncio
async def test_trusted_jwks_from_file_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, private_jwk: dict[str, Any]
) -> None:
    jwks_file = tmp_path / "trusted-jwks.json"
    jwks_file.write_text(json.dumps({"keys": [{**private_jwk, "kid": "trusted-kid"}]}), encoding="utf-8")
    monkeypatch.setattr(settings, "A2A_TRUSTED_JWKS", "")
    monkeypatch.setattr(settings, "A2A_TRUSTED_JWKS_PATH", str(jwks_file))

    card_data = sign_agent_card(_card_data(), private_jwk, kid="trusted-kid")
    _install_remote(monkeypatch, card_data)
    card = await discover_agent_card(BASE_URL, verify_signature=True)
    assert card.verified is True
