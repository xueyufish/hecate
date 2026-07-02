"""Unit tests for OutputSchemaValidator."""

from __future__ import annotations

from hecate.services.validation.output_validator import OutputSchemaValidator


class TestOutputSchemaValidator:
    """Tests for the OutputSchemaValidator class."""

    def test_validate_valid_json(self) -> None:
        """Test validation of valid JSON output."""
        validator = OutputSchemaValidator()
        result = validator.validate('{"name": "test"}')
        assert result.is_valid is True
        assert result.repaired is False

    def test_validate_invalid_json(self) -> None:
        """Test validation of invalid JSON output."""
        validator = OutputSchemaValidator()
        result = validator.validate("not json at all")
        assert result.is_valid is False

    def test_validate_empty_output(self) -> None:
        """Test validation of empty output."""
        validator = OutputSchemaValidator()
        result = validator.validate("")
        assert result.is_valid is False

    def test_validate_with_schema_pass(self) -> None:
        """Test validation with schema passes."""
        validator = OutputSchemaValidator()
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
        }
        result = validator.validate('{"name": "test"}', schema)
        assert result.is_valid is True

    def test_validate_with_schema_fail(self) -> None:
        """Test validation with schema fails."""
        validator = OutputSchemaValidator()
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }
        result = validator.validate('{"other": "value"}', schema)
        assert result.is_valid is False

    def test_auto_repair_trailing_comma(self) -> None:
        """Test auto-repair of trailing comma."""
        validator = OutputSchemaValidator()
        repaired, was_repaired = validator.auto_repair('{"key": "value",}')
        assert was_repaired is True
        assert '"key": "value"' in repaired

    def test_auto_repair_no_issues(self) -> None:
        """Test auto-repair when no issues exist."""
        validator = OutputSchemaValidator()
        repaired, was_repaired = validator.auto_repair('{"key": "value"}')
        assert was_repaired is False

    def test_validate_from_code_block(self) -> None:
        """Test validation of JSON from markdown code block."""
        validator = OutputSchemaValidator()
        output = '```json\n{"name": "test"}\n```'
        result = validator.validate(output)
        assert result.is_valid is True
        assert result.repaired is True
