"""A2A AgentCard discovery from remote endpoints."""

from __future__ import annotations

import base64
import binascii
import json
import logging

import httpx

from hecate.channel.a2a.types import AgentCard
from hecate.core.config import settings

logger = logging.getLogger(__name__)


async def discover_agent_card(
    base_url: str,
    timeout: float = 30.0,
    verify_signature: bool = False,
) -> AgentCard:
    """Fetch and parse an AgentCard from a remote A2A endpoint.

    Args:
        base_url: Base URL of the remote agent (e.g., "https://agent.example.com").
        timeout: HTTP request timeout in seconds.
        verify_signature: Whether to verify the card's JWS signature.
            Verification fails closed: without a trusted JWKS, without a
            card signature, or on any verification failure the discovery
            raises instead of returning the card.

    Returns:
        Parsed AgentCard object with ``verified`` set appropriately.

    Raises:
        httpx.HTTPStatusError: If the HTTP request fails.
        ValueError: If the card cannot be parsed or signature verification
            fails (missing, tampered, or untrusted signer).
    """
    well_known_url = f"{base_url.rstrip('/')}/.well-known/agent-card.json"

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(well_known_url)
        response.raise_for_status()

    data = response.json()

    # Parse into AgentCard
    card = AgentCard(
        name=data.get("name", ""),
        description=data.get("description", ""),
        version=data.get("version", ""),
        url=data.get("url", base_url),
        capabilities=data.get("capabilities", {}),
        skills=data.get("skills", []),
        security_schemes=data.get("securitySchemes", {}),
        default_input_modes=data.get("defaultInputModes", ["text/plain"]),
        default_output_modes=data.get("defaultOutputModes", ["text/plain"]),
    )

    if not verify_signature:
        logger.info("Agent Card from %s accepted WITHOUT signature verification", base_url)
        return card

    # Fail closed: verification demanded, so every gap is an error.
    from hecate.channel.a2a.signing import verify_agent_card_signature

    signatures = data.get("signatures") or []
    if not signatures:
        raise ValueError("Agent Card is unsigned but signature verification was requested")

    trusted = settings.a2a_trusted_jwks_map
    if not trusted:
        raise ValueError(
            "Agent Card signature verification requested but no trusted JWKS is configured "
            "(set A2A_TRUSTED_JWKS or A2A_TRUSTED_JWKS_PATH)"
        )

    for signature in signatures:
        kid = _signature_kid(signature)
        public_key = trusted.get(kid) if kid else None
        if public_key is None:
            continue
        # The signing helper verifies signatures[0]; feed one signature at a
        # time so multi-signature cards are fully checked against the trust set.
        if verify_agent_card_signature({**data, "signatures": [signature]}, public_key):
            card.verified = True
            return card

    raise ValueError(
        "Agent Card signature verification failed: signature missing, tampered, or signed by an untrusted issuer"
    )


def _signature_kid(signature: dict) -> str | None:
    """Extract the key id from a JWS signature's protected header."""
    protected_b64 = signature.get("protected", "")
    if not protected_b64:
        return None
    try:
        protected = json.loads(base64.urlsafe_b64decode(protected_b64 + "=="))
    except (ValueError, binascii.Error, json.JSONDecodeError):
        return None
    kid = protected.get("kid")
    return str(kid) if kid else None
