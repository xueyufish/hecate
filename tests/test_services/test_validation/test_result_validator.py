"""Unit tests for ResultValidator."""

from __future__ import annotations

from hecate.services.validation.result_validator import ResultValidator


class TestResultValidator:
    """Tests for the ResultValidator class."""

    def test_validate_no_schema(self) -> None:
        """Test validation without schema passes."""
        validator = ResultValidator()
        result = validator.validate({"key": "value"})
        assert result.is_valid is True

    def test_validate_valid_schema(self) -> None:
        """Test validation with valid schema passes."""
        validator = ResultValidator()
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }
        result = validator.validate({"name": "test"}, schema)
        assert result.is_valid is True

    def test_validate_invalid_schema(self) -> None:
        """Test validation with invalid schema fails."""
        validator = ResultValidator()
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }
        result = validator.validate({"other": "value"}, schema)
        assert result.is_valid is False
        assert len(result.errors) > 0

    def test_validate_with_rules_pass(self) -> None:
        """Test validation with custom rules passes."""
        validator = ResultValidator()
        rules = [
            {"name": "non_empty", "predicate": lambda x: len(str(x)) > 0},
        ]
        result = validator.validate_with_rules("Hello", rules)
        assert result.is_valid is True

    def test_validate_with_rules_fail(self) -> None:
        """Test validation with custom rules fails."""
        validator = ResultValidator()
        rules = [
            {"name": "non_empty", "predicate": lambda x: len(str(x)) > 0},
            {"name": "starts_with_A", "predicate": lambda x: str(x).startswith("A")},
        ]
        result = validator.validate_with_rules("Hello", rules)
        assert result.is_valid is False
        assert len(result.errors) == 1
