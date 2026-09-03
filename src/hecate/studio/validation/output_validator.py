"""Output schema validator for LLM responses.

Validates LLM outputs against expected schemas and provides
auto-repair for common format errors (missing quotes, trailing commas).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


class ValidationResult:
    """Result of a validation operation."""

    def __init__(
        self,
        is_valid: bool,
        errors: list[str] | None = None,
        repaired: bool = False,
    ) -> None:
        self.is_valid = is_valid
        self.errors = errors or []
        self.repaired = repaired


class OutputSchemaValidator:
    """Validates and repairs LLM outputs.

    Provides:
    - JSON parsing with auto-repair
    - Schema validation against expected format
    """

    def validate(
        self,
        output: str,
        schema: dict[str, Any] | None = None,
    ) -> ValidationResult:
        """Validate LLM output.

        Args:
            output: Raw LLM output string.
            schema: Optional JSON Schema to validate against.

        Returns:
            ValidationResult with is_valid, errors, and repaired flag.
        """
        if not output:
            return ValidationResult(is_valid=False, errors=["Empty output"])

        # Try to parse as JSON
        parsed, repaired = self._try_parse_json(output)

        if parsed is None:
            return ValidationResult(
                is_valid=False,
                errors=["Invalid JSON format"],
                repaired=repaired,
            )

        # Validate against schema if provided
        if schema:
            try:
                import jsonschema

                jsonschema.validate(instance=parsed, schema=schema)
            except jsonschema.ValidationError as e:
                return ValidationResult(
                    is_valid=False,
                    errors=[f"Schema validation failed: {e.message}"],
                    repaired=repaired,
                )

        return ValidationResult(is_valid=True, repaired=repaired)

    def auto_repair(self, output: str) -> tuple[str, bool]:
        """Attempt to repair common JSON format errors.

        Args:
            output: Raw output string.

        Returns:
            Tuple of (repaired_string, was_repaired).
        """
        repaired = output
        was_repaired = False

        # Fix trailing commas
        fixed = re.sub(r",\s*([}\]])", r"\1", repaired)
        if fixed != repaired:
            repaired = fixed
            was_repaired = True

        # Fix missing quotes on keys
        fixed = re.sub(r"(\w+)\s*:", r'"\1":', repaired)
        if fixed != repaired:
            repaired = fixed
            was_repaired = True

        return repaired, was_repaired

    def _try_parse_json(self, output: str) -> tuple[Any, bool]:
        """Try to parse JSON, with auto-repair attempts.

        Args:
            output: Raw string to parse.

        Returns:
            Tuple of (parsed_value_or_None, was_repaired).
        """
        # Try direct parse
        try:
            return json.loads(output), False
        except (json.JSONDecodeError, TypeError):
            pass

        # Try with auto-repair
        repaired, was_repaired = self.auto_repair(output)
        if was_repaired:
            try:
                return json.loads(repaired), True
            except (json.JSONDecodeError, TypeError):
                pass

        # Try extracting JSON from markdown code blocks
        match = re.search(r"```(?:json)?\s*\n(.*?)\n```", output, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1)), True
            except (json.JSONDecodeError, TypeError):
                pass

        return None, False
