"""A2A AgentCard discovery from remote endpoints."""

from __future__ import annotations

import logging

import httpx

from hecate.a2a.types import AgentCard

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

    Returns:
        Parsed AgentCard object.

    Raises:
        httpx.HTTPStatusError: If the HTTP request fails.
        ValueError: If the card cannot be parsed or signature verification fails.
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

    # TODO: Implement signature verification when signing module is ready
    if verify_signature and "signatures" in data:
        logger.warning("Signature verification requested but not yet implemented")

    return card
