"""Tests for A2A push notification webhook receiver."""

from __future__ import annotations

from hecate.a2a.client.push import router


def test_push_webhook_router_exists() -> None:
    """Test that push notification router is importable."""
    assert router is not None
