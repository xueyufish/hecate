"""Tests for RegexRecognizer."""

from __future__ import annotations

from hecate.services.security.dlp.recognizers.regex import (
    RegexRecognizer,
    _luhn_check,
)
from hecate.services.security.dlp.result import DLPFinding


class TestLuhnCheck:
    def test_valid_visa_test_card(self) -> None:
        assert _luhn_check("4111111111111111") is True

    def test_valid_mastercard_test_card(self) -> None:
        assert _luhn_check("5555555555554444") is True

    def test_invalid_luhn(self) -> None:
        assert _luhn_check("4111111111111112") is False

    def test_all_ones_fails(self) -> None:
        assert _luhn_check("1111111111111111") is False

    def test_all_zeros_passes(self) -> None:
        assert _luhn_check("0000000000000000") is True


class TestRegexRecognizerMetadata:
    def test_name(self) -> None:
        assert RegexRecognizer.name == "regex_pii"

    def test_supported_entities(self) -> None:
        expected = {"EMAIL", "PHONE", "CREDIT_CARD", "SSN", "IP_ADDRESS", "CHINA_ID_CARD"}
        assert set(RegexRecognizer.supported_entities) == expected


class TestRegexRecognizerEmail:
    def test_detects_email(self) -> None:
        rec = RegexRecognizer()
        findings = rec.analyze("Contact john.doe@example.com for details")
        assert len(findings) == 1
        assert findings[0].entity_type == "EMAIL"
        assert findings[0].value == "john.doe@example.com"
        assert findings[0].score == 1.0
        assert findings[0].recognizer == "regex_pii"

    def test_no_email_returns_empty(self) -> None:
        rec = RegexRecognizer()
        assert rec.analyze("no email here") == []

    def test_multiple_emails(self) -> None:
        rec = RegexRecognizer()
        text = "From a@b.com to c@d.org please reply"
        findings = rec.analyze(text)
        emails = [f for f in findings if f.entity_type == "EMAIL"]
        assert len(emails) == 2


class TestRegexRecognizerPhone:
    def test_detects_dashed_phone(self) -> None:
        rec = RegexRecognizer()
        findings = rec.analyze("Call 555-123-4567 today")
        assert len(findings) == 1
        assert findings[0].entity_type == "PHONE"
        assert findings[0].value == "555-123-4567"

    def test_detects_dotted_phone(self) -> None:
        rec = RegexRecognizer()
        findings = rec.analyze("Phone: 555.123.4567")
        assert len(findings) == 1
        assert findings[0].entity_type == "PHONE"


class TestRegexRecognizerSSN:
    def test_detects_ssn(self) -> None:
        rec = RegexRecognizer()
        findings = rec.analyze("SSN: 123-45-6789")
        assert len(findings) == 1
        assert findings[0].entity_type == "SSN"
        assert findings[0].value == "123-45-6789"


class TestRegexRecognizerIPAddress:
    def test_detects_ip(self) -> None:
        rec = RegexRecognizer()
        findings = rec.analyze("Server at 192.168.1.100 is down")
        assert len(findings) == 1
        assert findings[0].entity_type == "IP_ADDRESS"
        assert findings[0].value == "192.168.1.100"

    def test_high_octet_matches_pattern(self) -> None:
        rec = RegexRecognizer()
        findings = rec.analyze("999.999.999.999")
        assert len(findings) == 1
        assert findings[0].entity_type == "IP_ADDRESS"


class TestRegexRecognizerCreditCard:
    def test_detects_valid_visa(self) -> None:
        rec = RegexRecognizer()
        findings = rec.analyze("Card: 4111-1111-1111-1111")
        cards = [f for f in findings if f.entity_type == "CREDIT_CARD"]
        assert len(cards) == 1
        assert cards[0].value == "4111-1111-1111-1111"

    def test_detects_valid_mastercard(self) -> None:
        rec = RegexRecognizer()
        findings = rec.analyze("Pay with 5555555555554444")
        cards = [f for f in findings if f.entity_type == "CREDIT_CARD"]
        assert len(cards) == 1

    def test_filters_invalid_luhn(self) -> None:
        rec = RegexRecognizer()
        findings = rec.analyze("Card: 4111-1111-1111-1112")
        cards = [f for f in findings if f.entity_type == "CREDIT_CARD"]
        assert cards == []

    def test_filters_arbitrary_16_digits(self) -> None:
        rec = RegexRecognizer()
        findings = rec.analyze("Number 1234567890123456")
        cards = [f for f in findings if f.entity_type == "CREDIT_CARD"]
        assert cards == []


class TestRegexRecognizerChinaIdCard:
    def test_detects_18_digit_id(self) -> None:
        rec = RegexRecognizer()
        findings = rec.analyze("ID: 11010519491231002X")
        cards = [f for f in findings if f.entity_type == "CHINA_ID_CARD"]
        assert len(cards) == 1
        assert cards[0].value == "11010519491231002X"

    def test_detects_all_digit_id(self) -> None:
        rec = RegexRecognizer()
        findings = rec.analyze("公民身份号码 110105194912310028")
        cards = [f for f in findings if f.entity_type == "CHINA_ID_CARD"]
        assert len(cards) == 1

    def test_short_sequences_not_detected(self) -> None:
        rec = RegexRecognizer()
        assert rec.analyze("ID 12345") == []


class TestRegexRecognizerMixed:
    def test_multiple_entity_types_in_one_text(self) -> None:
        rec = RegexRecognizer()
        text = "Email user@example.com, phone 555-123-4567, IP 10.0.0.1"
        findings = rec.analyze(text)
        types = {f.entity_type for f in findings}
        assert "EMAIL" in types
        assert "PHONE" in types
        assert "IP_ADDRESS" in types

    def test_entity_filter_excludes_other_types(self) -> None:
        rec = RegexRecognizer()
        text = "Email user@example.com, phone 555-123-4567"
        findings = rec.analyze(text, entities=["EMAIL"])
        assert all(f.entity_type == "EMAIL" for f in findings)
        assert len(findings) == 1

    def test_empty_text_returns_empty(self) -> None:
        rec = RegexRecognizer()
        assert rec.analyze("") == []

    def test_recognizer_name_attribution(self) -> None:
        rec = RegexRecognizer()
        findings = rec.analyze("user@example.com")
        assert all(f.recognizer == rec.name for f in findings)

    def test_finding_positions_are_correct(self) -> None:
        rec = RegexRecognizer()
        text = "Hello user@example.com world"
        findings = rec.analyze(text)
        assert len(findings) == 1
        finding = findings[0]
        assert finding.start == 6
        assert finding.end == 6 + len("user@example.com")
        assert text[finding.start : finding.end] == "user@example.com"

    def test_returns_dlp_finding_instances(self) -> None:
        rec = RegexRecognizer()
        findings = rec.analyze("user@example.com")
        assert all(isinstance(f, DLPFinding) for f in findings)
