"""Tests for A2A management API endpoints."""

from __future__ import annotations

from hecate.api.management.a2a import router


def test_a2a_management_router_exists() -> None:
    """Test that A2A management router is importable."""
    assert router is not None
    assert router.prefix == "/api/a2a"


def test_a2a_management_router_tags() -> None:
    """Test that A2A management router has correct tags."""
    assert "a2a-management" in router.tags
