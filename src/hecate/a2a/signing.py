"""A2A AgentCard signing and verification using JWS (RFC 7515).

Provides ES256 key pair generation, JWS signing with RFC 8785
JSON Canonicalization, and signature verification with algorithm pinning.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def generate_es256_keypair() -> tuple[dict[str, Any], dict[str, Any]]:
    """Generate an ECDSA P-256 key pair for ES256 signing.

    Returns:
        Tuple of (private_key_jwk, public_key_jwk) in JWK format.
    """
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.asymmetric import ec

    private_key = ec.generate_private_key(ec.SECP256R1(), default_backend())
    public_key = private_key.public_key()

    private_numbers = private_key.private_numbers()
    public_numbers = public_key.public_numbers()

    def int_to_base64url(n: int, length: int = 32) -> str:
        return base64.urlsafe_b64encode(n.to_bytes(length, "big")).rstrip(b"=").decode()

    private_jwk = {
        "kty": "EC",
        "crv": "P-256",
        "d": int_to_base64url(private_numbers.private_value),
        "x": int_to_base64url(public_numbers.x),
        "y": int_to_base64url(public_numbers.y),
        "alg": "ES256",
    }

    public_jwk = {
        "kty": "EC",
        "crv": "P-256",
        "x": int_to_base64url(public_numbers.x),
        "y": int_to_base64url(public_numbers.y),
        "alg": "ES256",
    }

    return private_jwk, public_jwk


def canonicalize_json(data: dict[str, Any]) -> bytes:
    """Canonicalize JSON using RFC 8785 JSON Canonicalization Scheme.

    Args:
        data: The JSON data to canonicalize.

    Returns:
        Canonicalized JSON bytes.
    """
    return json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")


def sign_agent_card(
    card_data: dict[str, Any],
    private_key_jwk: dict[str, Any],
    kid: str = "",
) -> dict[str, Any]:
    """Sign an AgentCard using JWS with ES256 algorithm.

    Args:
        card_data: The AgentCard dict to sign (without signatures field).
        private_key_jwk: The private key in JWK format.
        kid: Optional key ID for the signature.

    Returns:
        The card_data with a signatures array added.
    """

    # Remove existing signatures before signing
    card_to_sign = {k: v for k, v in card_data.items() if k != "signatures"}

    # Canonicalize the card
    canonical = canonicalize_json(card_to_sign)

    # Create JWS protected header
    protected_header = {"alg": "ES256", "kid": kid} if kid else {"alg": "ES256"}
    protected_b64 = base64.urlsafe_b64encode(json.dumps(protected_header).encode()).rstrip(b"=").decode()

    # Sign the canonicalized card
    signature = _es256_sign(canonical, private_key_jwk)
    signature_b64 = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()

    # Add signature to card
    card_data["signatures"] = [
        {
            "protected": protected_b64,
            "signature": signature_b64,
        }
    ]

    return card_data


def verify_agent_card_signature(
    card_data: dict[str, Any],
    public_key_jwk: dict[str, Any],
) -> bool:
    """Verify an AgentCard's JWS signature.

    Args:
        card_data: The AgentCard dict with signatures array.
        public_key_jwk: The public key in JWK format.

    Returns:
        True if signature is valid, False otherwise.

    Raises:
        ValueError: If algorithm is not ES256 (alg pinning).
    """
    signatures = card_data.get("signatures", [])
    if not signatures:
        return False

    # Get the first signature
    sig = signatures[0]
    protected_b64 = sig.get("protected", "")
    signature_b64 = sig.get("signature", "")

    # Decode protected header
    protected_json = base64.urlsafe_b64decode(protected_b64 + "==")
    protected_header = json.loads(protected_json)

    # Algorithm pinning - only ES256 allowed
    alg = protected_header.get("alg", "")
    if alg != "ES256":
        raise ValueError(f"Algorithm not allowed: {alg}. Only ES256 is accepted.")

    # Reconstruct canonical form (without signatures)
    card_to_verify = {k: v for k, v in card_data.items() if k != "signatures"}
    canonical = canonicalize_json(card_to_verify)

    # Decode signature
    signature = base64.urlsafe_b64decode(signature_b64 + "==")

    # Verify
    return _es256_verify(canonical, signature, public_key_jwk)


def _es256_sign(data: bytes, private_key_jwk: dict[str, Any]) -> bytes:
    """Sign data using ES256 (ECDSA P-256 + SHA-256)."""
    import base64

    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec

    # Reconstruct private key from JWK
    d_bytes = base64.urlsafe_b64decode(private_key_jwk["d"] + "==")
    x_bytes = base64.urlsafe_b64decode(private_key_jwk["x"] + "==")
    y_bytes = base64.urlsafe_b64decode(private_key_jwk["y"] + "==")

    from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateNumbers, EllipticCurvePublicNumbers

    d_int = int.from_bytes(d_bytes, "big")
    x_int = int.from_bytes(x_bytes, "big")
    y_int = int.from_bytes(y_bytes, "big")

    public_numbers = EllipticCurvePublicNumbers(x_int, y_int, ec.SECP256R1())
    private_numbers = EllipticCurvePrivateNumbers(d_int, public_numbers)
    private_key = private_numbers.private_key(default_backend())

    # Sign
    signature = private_key.sign(data, ec.ECDSA(hashes.SHA256()))
    return signature


def _es256_verify(data: bytes, signature: bytes, public_key_jwk: dict[str, Any]) -> bool:
    """Verify ES256 signature."""
    import base64

    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec

    try:
        x_bytes = base64.urlsafe_b64decode(public_key_jwk["x"] + "==")
        y_bytes = base64.urlsafe_b64decode(public_key_jwk["y"] + "==")

        x_int = int.from_bytes(x_bytes, "big")
        y_int = int.from_bytes(y_bytes, "big")

        from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePublicNumbers

        public_numbers = EllipticCurvePublicNumbers(x_int, y_int, ec.SECP256R1())
        public_key = public_numbers.public_key(default_backend())

        public_key.verify(signature, data, ec.ECDSA(hashes.SHA256()))
        return True
    except Exception as e:
        logger.warning("Signature verification failed: %s", e)
        return False


def generate_jwks(public_keys: list[dict[str, Any]]) -> dict[str, Any]:
    """Generate a JWKS document from a list of public keys.

    Args:
        public_keys: List of public key JWK dicts.

    Returns:
        JWKS document with keys array.
    """
    return {"keys": public_keys}
