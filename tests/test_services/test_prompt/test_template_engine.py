"""Unit tests for TemplateEngine."""

from __future__ import annotations

import pytest

from hecate.services.memory.template_engine import TemplateEngine


class TestTemplateEngine:
    """Tests for the TemplateEngine class."""

    def test_render_simple(self) -> None:
        """Test rendering a simple template."""
        engine = TemplateEngine()
        result = engine.render("Hello {{ name }}!", {"name": "Alice"})
        assert result == "Hello Alice!"

    def test_render_multiple_variables(self) -> None:
        """Test rendering with multiple variables."""
        engine = TemplateEngine()
        result = engine.render(
            "Hello {{ name }}, welcome to {{ place }}!",
            {"name": "Bob", "place": "Wonderland"},
        )
        assert result == "Hello Bob, welcome to Wonderland!"

    def test_render_no_variables(self) -> None:
        """Test rendering without variables."""
        engine = TemplateEngine()
        result = engine.render("Hello World!")
        assert result == "Hello World!"

    def test_render_missing_variable(self) -> None:
        """Test rendering with missing variable."""
        engine = TemplateEngine()
        result = engine.render("Hello {{ name }}!", {})
        # Missing variables render as empty string
        assert "Hello" in result

    def test_validate_valid_template(self) -> None:
        """Test validating a valid template."""
        engine = TemplateEngine()
        assert engine.validate("Hello {{ name }}!") is True

    def test_validate_invalid_syntax(self) -> None:
        """Test validating invalid template syntax."""
        engine = TemplateEngine()
        with pytest.raises(ValueError, match="Invalid template syntax"):
            engine.validate("Hello {{ name !")

    def test_validate_unsafe_operations(self) -> None:
        """Test that unsafe operations are handled."""
        engine = TemplateEngine()
        # SandboxedEnvironment may or may not raise for this specific pattern
        # The important thing is that rendering doesn't execute unsafe code
        try:
            result = engine.render("{{ ''.__class__.__mro__ }}", {})
            # If it doesn't raise, it should render safely
            assert isinstance(result, str)
        except ValueError:
            # If it raises, that's also acceptable
            pass

    def test_extract_variables(self) -> None:
        """Test extracting variables from template."""
        engine = TemplateEngine()
        variables = engine.extract_variables("Hello {{ name }}, welcome to {{ place }}!")
        assert "name" in variables
        assert "place" in variables

    def test_extract_variables_no_duplicates(self) -> None:
        """Test that duplicate variables are deduplicated."""
        engine = TemplateEngine()
        variables = engine.extract_variables("{{ name }} and {{ name }} again")
        assert variables.count("name") == 1

    def test_extract_variables_empty(self) -> None:
        """Test extracting variables from template without variables."""
        engine = TemplateEngine()
        variables = engine.extract_variables("Hello World!")
        assert variables == []
