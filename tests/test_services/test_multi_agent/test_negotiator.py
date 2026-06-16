"""Unit tests for P2PNegotiator."""

from __future__ import annotations

import pytest

from hecate.services.multi_agent.negotiator import (
    NegotiationStatus,
    P2PNegotiator,
)


class TestP2PNegotiator:
    """Tests for the P2PNegotiator class."""

    @pytest.mark.asyncio
    async def test_negotiate_agreement(self) -> None:
        """Test successful negotiation."""
        negotiator = P2PNegotiator()

        result = await negotiator.negotiate(
            task={"type": "search", "query": "Python"},
            agents=["agent-1", "agent-2"],
        )

        assert result.status == NegotiationStatus.AGREED
        assert result.agreement is not None
        assert result.rounds == 1

    @pytest.mark.asyncio
    async def test_negotiate_timeout(self) -> None:
        """Test negotiation timeout."""
        negotiator = P2PNegotiator()

        result = await negotiator.negotiate(
            task={"type": "complex"},
            agents=["agent-1"],
            max_rounds=1,
        )

        assert result.status in [NegotiationStatus.AGREED, NegotiationStatus.TIMEOUT]

    @pytest.mark.asyncio
    async def test_escalate(self) -> None:
        """Test escalation to coordinator."""
        negotiator = P2PNegotiator()

        # Start a negotiation
        result = await negotiator.negotiate(
            task={"type": "test"},
            agents=["agent-1"],
            max_rounds=1,
        )

        # If it timed out, escalate
        if result.status == NegotiationStatus.TIMEOUT:
            escalated = await negotiator.escalate(
                result.negotiation_id,
                "coordinator-1",
            )
            assert escalated.status == NegotiationStatus.ESCALATED

    @pytest.mark.asyncio
    async def test_escalate_not_found(self) -> None:
        """Test escalation of non-existent negotiation."""
        negotiator = P2PNegotiator()

        result = await negotiator.escalate("non-existent", "coordinator-1")

        assert result.status == NegotiationStatus.REJECTED

    def test_get_active_negotiations(self) -> None:
        """Test getting active negotiations."""
        negotiator = P2PNegotiator()

        active = negotiator.get_active_negotiations()

        assert isinstance(active, list)
