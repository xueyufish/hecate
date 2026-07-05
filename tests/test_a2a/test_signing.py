"""Tests for A2A AgentCard signing and verification."""

from __future__ import annotations

from hecate.a2a.signing import (
    canonicalize_json,
    generate_es256_keypair,
    generate_jwks,
    sign_agent_card,
    verify_agent_card_signature,
)


def test_generate_es256_keypair() -> None:
    """Test ES256 key pair generation."""
    private_jwk, public_jwk = generate_es256_keypair()
    assert private_jwk["kty"] == "EC"
    assert private_jwk["crv"] == "P-256"
    assert private_jwk["alg"] == "ES256"
    assert "d" in private_jwk
    assert "x" in private_jwk
    assert "y" in private_jwk
    assert public_jwk["kty"] == "EC"
    assert public_jwk["crv"] == "P-256"
    assert "d" not in public_jwk


def test_canonicalize_json() -> None:
    """Test JSON canonicalization."""
    data = {"b": 2, "a": 1}
    canonical = canonicalize_json(data)
    assert canonical == b'{"a":1,"b":2}'


def test_sign_and_verify_agent_card() -> None:
    """Test signing and verification round trip."""
    private_jwk, public_jwk = generate_es256_keypair()

    card = {
        "name": "Test Agent",
        "description": "A test agent",
        "version": "1.0.0",
    }

    signed_card = sign_agent_card(card, private_jwk, kid="key-1")
    assert "signatures" in signed_card
    assert len(signed_card["signatures"]) == 1

    # Verify
    is_valid = verify_agent_card_signature(signed_card, public_jwk)
    assert is_valid is True


def test_verify_tampered_card() -> None:
    """Test that tampered card fails verification."""
    private_jwk, public_jwk = generate_es256_keypair()

    card = {"name": "Test Agent"}
    signed_card = sign_agent_card(card, private_jwk)

    # Tamper with the card
    signed_card["name"] = "Tampered Agent"

    is_valid = verify_agent_card_signature(signed_card, public_jwk)
    assert is_valid is False


def test_verify_wrong_key() -> None:
    """Test that verification fails with wrong public key."""
    private_jwk, _ = generate_es256_keypair()
    _, other_public_jwk = generate_es256_keypair()

    card = {"name": "Test Agent"}
    signed_card = sign_agent_card(card, private_jwk)

    is_valid = verify_agent_card_signature(signed_card, other_public_jwk)
    assert is_valid is False


def test_generate_jwks() -> None:
    """Test JWKS generation."""
    _, public_jwk = generate_es256_keypair()
    jwks = generate_jwks([public_jwk])
    assert "keys" in jwks
    assert len(jwks["keys"]) == 1
    assert jwks["keys"][0]["kty"] == "EC"
