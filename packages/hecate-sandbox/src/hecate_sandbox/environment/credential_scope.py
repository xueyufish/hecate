"""Credential scoping configuration for DockerEnvironment.

Controls which environment variables are passed to tool execution.
Secret-pattern variables are stripped; only per-tool scoped credentials
are injected. This prevents tools from accessing secrets they don't need.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field

# Default patterns that identify secret environment variables.
# Variables matching these suffixes/prefixes are stripped when
# credential scoping is enabled.
DEFAULT_STRIP_PATTERNS: list[str] = [
    "*_KEY",
    "*_SECRET",
    "*_TOKEN",
    "*_PASSWORD",
    "*_API_KEY",
    "*_PWD",
]

# Explicit prefix for marking secrets that don't match default patterns.
SECRET_PREFIX = "HECATE_SECRET_"  # noqa: S105

# System variables that are ALWAYS preserved regardless of stripping.
# These are essential for process execution and must not be removed.
SYSTEM_WHITELIST: frozenset[str] = frozenset(
    {
        "PATH",
        "HOME",
        "LANG",
        "TMPDIR",
        "USER",
        "SHELL",
        "HOSTNAME",
        "TERM",
        "PWD",
        "SHLVL",
        "_",
    }
)

# LC_* variables are also always preserved (locale settings).
LC_PATTERN = re.compile(r"^LC_[A-Z]+$")


@dataclass
class CredentialScope:
    """Configuration for per-execution credential isolation.

    Attributes:
        enabled: Whether credential scoping is active.
        strip_patterns: Glob patterns for env var names to strip.
            Defaults to common secret naming conventions.
        custom_patterns: Additional workspace-specific strip patterns.
        tool_credentials: Maps tool name to list of credential variable
            names that tool is allowed to receive.
    """

    enabled: bool = False
    strip_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_STRIP_PATTERNS))
    custom_patterns: list[str] = field(default_factory=list)
    tool_credentials: dict[str, list[str]] = field(default_factory=dict)

    def should_strip(self, var_name: str) -> bool:
        """Check if an environment variable should be stripped.

        Args:
            var_name: The environment variable name.

        Returns:
            True if the variable should be stripped (is a secret pattern
            and NOT in the system whitelist).
        """
        # System whitelist always preserved
        if var_name in SYSTEM_WHITELIST:
            return False
        if LC_PATTERN.match(var_name):
            return False

        # HECATE_SECRET_ prefix always stripped
        if var_name.startswith(SECRET_PREFIX):
            return True

        all_patterns = self.strip_patterns + self.custom_patterns
        return any(fnmatch.fnmatch(var_name, pattern) for pattern in all_patterns)

    def sanitize_environment(
        self,
        env: dict[str, str],
        tool_name: str | None = None,
    ) -> dict[str, str]:
        """Build a sanitized environment dict for tool execution.

        Args:
            env: The full process environment dictionary.
            tool_name: The tool being executed. If the tool has configured
                credentials in ``tool_credentials``, those are explicitly
                added even if they match strip patterns.

        Returns:
            Sanitized environment dictionary with secrets removed and
            tool-specific credentials injected.
        """
        if not self.enabled:
            return dict(env)

        sanitized: dict[str, str] = {}

        # First pass: copy non-stripped variables
        for key, value in env.items():
            if not self.should_strip(key):
                sanitized[key] = value

        # Second pass: inject tool-specific credentials (if configured)
        if tool_name and tool_name in self.tool_credentials:
            for cred_name in self.tool_credentials[tool_name]:
                if cred_name in env:
                    sanitized[cred_name] = env[cred_name]

        return sanitized
