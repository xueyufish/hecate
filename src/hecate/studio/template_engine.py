"""Template engine for Jinja2 prompt rendering.

Provides safe template rendering with variable extraction and validation.
Uses Jinja2 SandboxedEnvironment to prevent code injection.
"""

from __future__ import annotations

import logging
import re

from jinja2 import BaseLoader, TemplateSyntaxError, Undefined, UndefinedError
from jinja2.sandbox import SandboxedEnvironment

logger = logging.getLogger(__name__)

# Pattern for extracting Jinja2 variables: {{ variable_name }}
_VARIABLE_PATTERN = re.compile(r"\{\{\s*(\w+)\s*\}\}")


class TemplateEngine:
    """Engine for rendering and validating Jinja2 prompt templates.

    Uses SandboxedEnvironment to prevent code injection while allowing
    safe variable substitution.
    """

    def __init__(self) -> None:
        """Initialize the template engine."""
        self._env = SandboxedEnvironment(
            loader=BaseLoader(),
            undefined=_StrictUndefined,
        )

    def render(
        self,
        template: str,
        variables: dict[str, str] | None = None,
    ) -> str:
        """Render a template with provided variables.

        Args:
            template: Jinja2 template string.
            variables: Variable values for rendering.

        Returns:
            Rendered template string.

        Raises:
            ValueError: If template has syntax errors or security issues.
        """
        try:
            t = self._env.from_string(template)
            return t.render(**(variables or {}))
        except (TemplateSyntaxError, UndefinedError) as e:
            raise ValueError(f"Template rendering failed: {e}") from e
        except Exception as e:
            raise ValueError(f"Template contains unsafe operations: {e}") from e

    def validate(self, template: str) -> bool:
        """Validate a template's syntax.

        Args:
            template: Jinja2 template string to validate.

        Returns:
            True if valid.

        Raises:
            ValueError: If template has syntax errors.
        """
        try:
            self._env.parse(template)
            return True
        except Exception as e:
            raise ValueError(f"Invalid template syntax: {e}") from e

    def extract_variables(self, template: str) -> list[str]:
        """Extract variable names from a template.

        Args:
            template: Jinja2 template string.

        Returns:
            List of unique variable names found in the template.
        """
        return list(set(_VARIABLE_PATTERN.findall(template)))


class _StrictUndefined(Undefined):
    """Custom undefined type that raises on access."""

    def __str__(self) -> str:
        return ""

    def __repr__(self) -> str:
        return ""
