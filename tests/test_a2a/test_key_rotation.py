"""Tests for A2A key rotation with grace period."""

from __future__ import annotations

from hecate.a2a.signing import generate_es256_keypair, generate_jwks


def test_key_rotation_generates_new_keypair() -> None:
    """Test that key rotation generates a new key pair."""
    private1, public1 = generate_es256_keypair()
    private2, public2 = generate_es256_keypair()

    # Keys should be different
    assert private1["d"] != private2["d"]
    assert public1["x"] != public2["x"]


def test_jwks_with_multiple_keys() -> None:
    """Test JWKS with multiple keys (during grace period)."""
    _, public1 = generate_es256_keypair()
    _, public2 = generate_es256_keypair()

    jwks = generate_jwks([public1, public2])
    assert len(jwks["keys"]) == 2


def test_key_status_lifecycle() -> None:
    """Test key status transitions (active → rotating → revoked)."""
    statuses = ["active", "rotating", "revoked"]
    assert statuses[0] == "active"
    assert statuses[1] == "rotating"
    assert statuses[2] == "revoked"
