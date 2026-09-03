"""Tests for DictionaryRecognizer."""

from __future__ import annotations

from hecate.ops.dlp.recognizers.dictionary import DictionaryRecognizer


class TestDictionaryRecognizerMetadata:
    def test_default_name(self) -> None:
        rec = DictionaryRecognizer(terms=["foo"])
        assert rec.name == "dictionary"

    def test_custom_name(self) -> None:
        rec = DictionaryRecognizer(terms=["foo"], name="custom_dict")
        assert rec.name == "custom_dict"

    def test_default_entity_type(self) -> None:
        rec = DictionaryRecognizer(terms=["foo"])
        assert rec.supported_entities == ["DICTIONARY"]

    def test_custom_entity_type(self) -> None:
        rec = DictionaryRecognizer(terms=["foo"], entity_type="CODENAME")
        assert rec.supported_entities == ["CODENAME"]


class TestDictionaryRecognizerExactMatch:
    def test_detects_single_term(self) -> None:
        rec = DictionaryRecognizer(terms=["ProjectX"])
        findings = rec.analyze("Discussing ProjectX today")
        assert len(findings) == 1
        assert findings[0].value == "ProjectX"
        assert findings[0].entity_type == "DICTIONARY"
        assert findings[0].score == 1.0
        assert findings[0].recognizer == "dictionary"

    def test_detects_multiple_terms(self) -> None:
        rec = DictionaryRecognizer(terms=["Alpha", "Beta", "Gamma"])
        findings = rec.analyze("Alpha and Beta are reviewed by Gamma")
        assert len(findings) == 3
        values = {f.value for f in findings}
        assert values == {"Alpha", "Beta", "Gamma"}

    def test_detects_multiple_occurrences(self) -> None:
        rec = DictionaryRecognizer(terms=["foo"])
        findings = rec.analyze("foo bar foo baz foo")
        assert len(findings) == 3
        assert all(f.value == "foo" for f in findings)

    def test_no_match_returns_empty(self) -> None:
        rec = DictionaryRecognizer(terms=["foo", "bar"])
        assert rec.analyze("baz qux") == []

    def test_empty_text_returns_empty(self) -> None:
        rec = DictionaryRecognizer(terms=["foo"])
        assert rec.analyze("") == []

    def test_empty_terms_returns_empty(self) -> None:
        rec = DictionaryRecognizer(terms=[])
        assert rec.analyze("anything goes") == []

    def test_empty_term_string_is_skipped(self) -> None:
        rec = DictionaryRecognizer(terms=["", "foo", ""])
        findings = rec.analyze("foo bar")
        assert len(findings) == 1
        assert findings[0].value == "foo"


class TestDictionaryRecognizerCaseSensitivity:
    def test_case_insensitive_default(self) -> None:
        rec = DictionaryRecognizer(terms=["SECRET"])
        findings = rec.analyze("Found a secret here")
        assert len(findings) == 1
        assert findings[0].value == "secret"

    def test_case_insensitive_uppercase_input(self) -> None:
        rec = DictionaryRecognizer(terms=["secret"])
        findings = rec.analyze("Found a SECRET here")
        assert len(findings) == 1

    def test_case_sensitive_does_not_match(self) -> None:
        rec = DictionaryRecognizer(terms=["SECRET"], case_sensitive=True)
        assert rec.analyze("Found a secret here") == []

    def test_case_sensitive_matches_exact(self) -> None:
        rec = DictionaryRecognizer(terms=["SECRET"], case_sensitive=True)
        findings = rec.analyze("Found a SECRET here")
        assert len(findings) == 1

    def test_case_insensitive_handles_mixed_case(self) -> None:
        rec = DictionaryRecognizer(terms=["ProjectX"])
        findings = rec.analyze("PROJECTX projectx ProjectX")
        assert len(findings) == 3


class TestDictionaryRecognizerBoundaries:
    def test_substring_not_detected(self) -> None:
        rec = DictionaryRecognizer(terms=["John"])
        assert rec.analyze("Johnny went home") == []

    def test_prefix_not_detected(self) -> None:
        rec = DictionaryRecognizer(terms=["cat"])
        assert rec.analyze("category is unclear") == []

    def test_suffix_not_detected(self) -> None:
        rec = DictionaryRecognizer(terms=["log"])
        assert rec.analyze("catalog of logs") == []

    def test_punctuation_is_boundary(self) -> None:
        rec = DictionaryRecognizer(terms=["SECRET"])
        findings = rec.analyze("Marked.SECRET.doc")
        assert len(findings) == 1
        assert findings[0].value == "SECRET"

    def test_hyphen_is_boundary(self) -> None:
        rec = DictionaryRecognizer(terms=["top"])
        findings = rec.analyze("top-secret project")
        assert len(findings) == 1


class TestDictionaryRecognizerPosition:
    def test_position_correctness(self) -> None:
        rec = DictionaryRecognizer(terms=["foo"])
        text = "abc foo def"
        findings = rec.analyze(text)
        assert len(findings) == 1
        finding = findings[0]
        assert finding.start == 4
        assert finding.end == 7
        assert text[finding.start : finding.end] == "foo"

    def test_multiple_findings_ordered_by_position(self) -> None:
        rec = DictionaryRecognizer(terms=["alpha", "beta"])
        text = "alpha beta alpha beta"
        findings = rec.analyze(text)
        assert len(findings) == 4
        positions = [(f.start, f.end) for f in findings]
        assert positions == sorted(positions)


class TestDictionaryRecognizerEntityFilter:
    def test_filter_includes_custom_entity(self) -> None:
        rec = DictionaryRecognizer(terms=["foo"], entity_type="CODENAME")
        findings = rec.analyze("foo bar", entities=["CODENAME"])
        assert len(findings) == 1

    def test_filter_excludes_other_entity(self) -> None:
        rec = DictionaryRecognizer(terms=["foo"], entity_type="CODENAME")
        assert rec.analyze("foo bar", entities=["EMAIL"]) == []

    def test_filter_with_default_entity(self) -> None:
        rec = DictionaryRecognizer(terms=["foo"])
        findings = rec.analyze("foo bar", entities=["DICTIONARY"])
        assert len(findings) == 1


class TestDictionaryRecognizerSpecialChars:
    def test_term_with_dot(self) -> None:
        rec = DictionaryRecognizer(terms=["v1.0"])
        findings = rec.analyze("released v1.0 yesterday")
        assert len(findings) == 1

    def test_term_with_parens(self) -> None:
        rec = DictionaryRecognizer(terms=["foo(bar)"])
        findings = rec.analyze("see foo(bar) spec")
        assert len(findings) == 1

    def test_term_with_regex_chars(self) -> None:
        rec = DictionaryRecognizer(terms=["a+b"])
        findings = rec.analyze("expression a+b equals something")
        assert len(findings) == 1
