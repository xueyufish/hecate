"""Tests for security layer components.

Tests cover:
- PII anonymization/deanonymization
- LLM Guard scanner
- NeMo Guardrails configuration
- Security middleware
"""

from __future__ import annotations

import pytest

from hecate.services.security.anonymizer import pii_anonymizer
from hecate.services.security.llm_guard import llm_guard_scanner
from hecate.services.security.nemo_guardrails import nemo_config


def test_pii_anonymize_email() -> None:
    """Test email anonymization."""
    text = "Contact me at user@example.com"
    result = pii_anonymizer.anonymize(text)
    assert "user@example.com" not in result.text
    assert "EMAIL" in result.text
    assert len(result.mappings) == 1


def test_pii_anonymize_phone() -> None:
    """Test phone number anonymization."""
    text = "Call me at 555-123-4567"
    result = pii_anonymizer.anonymize(text)
    assert "555-123-4567" not in result.text
    assert "PHONE" in result.text


def test_pii_anonymize_multiple() -> None:
    """Test multiple PII types."""
    text = "Email: test@test.com, Phone: 555-123-4567"
    result = pii_anonymizer.anonymize(text)
    assert "test@test.com" not in result.text
    assert "555-123-4567" not in result.text


def test_pii_deanonymize() -> None:
    """Test PII restoration."""
    original = "Contact: user@example.com"
    anonymized = pii_anonymizer.anonymize(original)
    restored = pii_anonymizer.deanonymize(anonymized)
    assert restored == original


def test_pii_has_pii() -> None:
    """Test PII detection."""
    assert pii_anonymizer.has_pii("Email me at test@test.com") is True
    assert pii_anonymizer.has_pii("Hello world") is False


@pytest.mark.asyncio
async def test_llm_guard_scan_safe() -> None:
    """Test LLM Guard scanning safe content."""
    result = await llm_guard_scanner.scan_prompt("What is the weather today?")
    assert result.is_safe is True


@pytest.mark.asyncio
async def test_llm_guard_scan_unsafe() -> None:
    """Test LLM Guard scanning unsafe content."""
    result = await llm_guard_scanner.scan_prompt("How to hack into a system?")
    assert result.is_safe is False
    assert len(result.issues) > 0


@pytest.mark.asyncio
async def test_nemo_check_safe() -> None:
    """Test NeMo Guardrails safe input."""
    result = await nemo_config.check_input("Help me with my project")
    assert result is True


@pytest.mark.asyncio
async def test_nemo_check_unsafe() -> None:
    """Test NeMo Guardrails unsafe input."""
    result = await nemo_config.check_input("How to make a bomb?")
    assert result is False
