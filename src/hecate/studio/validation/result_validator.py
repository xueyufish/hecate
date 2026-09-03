"""Result validator for tool execution outputs.

Validates tool outputs against JSON Schema and custom rules.
Provides structured error reporting for invalid outputs.
"""

from __future__ import annotations

import logging
from typing import Any

import jsonschema

logger = logging.getLogger(__name__)


class ValidationResult:
    """Result of a validation operation."""

    def __init__(
        self,
        is_valid: bool,
        errors: list[str] | None = None,
    ) -> None:
        self.is_valid = is_valid
        self.errors = errors or []


class ResultValidator:
    """Validates tool execution results against schemas and rules.

    Supports:
    - JSON Schema validation
    - Custom rule validation (callable predicates)
    """

    def validate(
        self,
        output: Any,
        schema: dict[str, Any] | None = None,
    ) -> ValidationResult:
        """Validate output against a JSON Schema.

        Args:
            output: The tool output to validate.
            schema: JSON Schema to validate against.

        Returns:
            ValidationResult with is_valid and errors.
        """
        if schema is None:
            return ValidationResult(is_valid=True)

        try:
            jsonschema.validate(instance=output, schema=schema)
            return ValidationResult(is_valid=True)
        except jsonschema.ValidationError as e:
            return ValidationResult(
                is_valid=False,
                errors=[f"Schema validation failed: {e.message}"],
            )
        except Exception as e:
            return ValidationResult(
                is_valid=False,
                errors=[f"Validation error: {e!s}"],
            )

    def validate_with_rules(
        self,
        output: Any,
        rules: list[dict[str, Any]],
    ) -> ValidationResult:
        """Validate output against custom rules.

        Args:
            output: The tool output to validate.
            rules: List of rule dicts with 'name' and 'predicate' keys.

        Returns:
            ValidationResult with is_valid and errors.
        """
        errors: list[str] = []

        for rule in rules:
            name = rule.get("name", "unnamed")
            predicate = rule.get("predicate")

            if predicate and not predicate(output):
                errors.append(f"Rule '{name}' failed")

        return ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
        )
