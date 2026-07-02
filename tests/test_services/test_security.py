"""Tests for security layer components.

Tests cover:
- PII anonymization/deanonymization
- LLM Guard scanner
- Security middleware
"""

from __future__ import annotations

from hecate.services.security.anonymizer import pii_anonymizer
from hecate.services.security.llm_guard import llm_guard_scanner
from hecate.services.security.middleware import security_middleware


def test_pii_anonymize_email() -> None:
    text = "Contact me at user@example.com"
    result = pii_anonymizer.anonymize(text)
    assert "user@example.com" not in result.text
    assert "EMAIL" in result.text
    assert len(result.mappings) == 1


def test_pii_anonymize_phone() -> None:
    text = "Call me at 555-123-4567"
    result = pii_anonymizer.anonymize(text)
    assert "555-123-4567" not in result.text
    assert "PHONE" in result.text


def test_pii_anonymize_multiple() -> None:
    text = "Email: test@test.com, Phone: 555-123-4567"
    result = pii_anonymizer.anonymize(text)
    assert "test@test.com" not in result.text
    assert "555-123-4567" not in result.text


def test_pii_deanonymize() -> None:
    original = "Contact: user@example.com"
    anonymized = pii_anonymizer.anonymize(original)
    restored = pii_anonymizer.deanonymize(anonymized)
    assert restored == original


def test_pii_has_pii() -> None:
    assert pii_anonymizer.has_pii("Email me at test@test.com") is True
    assert pii_anonymizer.has_pii("Hello world") is False


async def test_llm_guard_scan_safe() -> None:
    result = await llm_guard_scanner.scan_prompt("What is the weather today?")
    assert result.is_safe is True


async def test_llm_guard_scan_unsafe() -> None:
    result = await llm_guard_scanner.scan_prompt("How to hack into a system?")
    assert result.is_safe is False
    assert len(result.issues) > 0


async def test_middleware_check_input_safe() -> None:
    result = await security_middleware.check_input("What is the weather today?")
    assert result["is_safe"] is True
    assert result["issues"] == []


async def test_middleware_check_input_unsafe() -> None:
    result = await security_middleware.check_input("How to hack into a system?")
    assert result["is_safe"] is False
    assert len(result["issues"]) > 0


async def test_middleware_check_output_safe() -> None:
    result = await security_middleware.check_output("The weather is sunny today.")
    assert result["is_safe"] is True


async def test_middleware_check_output_unsafe() -> None:
    result = await security_middleware.check_output("How to hack exploit bomb")
    assert result["is_safe"] is False


def test_scan_result_sanitized_text_default() -> None:
    from hecate.services.security.llm_guard import ScanResult

    result = ScanResult(is_safe=True, score=1.0, issues=[])
    assert result.sanitized_text is None


def test_scan_result_sanitized_text_set() -> None:
    from hecate.services.security.llm_guard import ScanResult

    result = ScanResult(is_safe=True, score=1.0, issues=[], sanitized_text="clean text")
    assert result.sanitized_text == "clean text"


async def test_scan_prompt_disabled_no_sanitized_text() -> None:
    from hecate.services.security.llm_guard import LLMGuardScanner

    scanner = LLMGuardScanner(enabled=False)
    result = await scanner.scan_prompt("test")
    assert result.sanitized_text is None


async def test_scan_output_disabled_no_sanitized_text() -> None:
    from hecate.services.security.llm_guard import LLMGuardScanner

    scanner = LLMGuardScanner(enabled=False)
    result = await scanner.scan_output("test")
    assert result.sanitized_text is None
