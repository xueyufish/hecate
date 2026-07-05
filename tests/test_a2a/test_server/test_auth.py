"""Tests for A2A authentication."""

from __future__ import annotations

from hecate.a2a.server.auth import verify_a2a_auth


def test_verify_a2a_auth_module_exists() -> None:
    """Test that auth module is importable."""
    assert callable(verify_a2a_auth)
