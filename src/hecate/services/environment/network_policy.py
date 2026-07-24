"""Network egress policy for DockerEnvironment.

Controls outbound network access from agent containers. When mode is
``deny_all``, only whitelisted domains are reachable through an egress
proxy; all other traffic is blocked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class NetworkPolicyMode(StrEnum):
    """Network egress policy mode."""

    ALLOW_ALL = "allow_all"
    DENY_ALL = "deny_all"


@dataclass
class NetworkEgressPolicy:
    """Configuration for DockerEnvironment network egress control.

    Attributes:
        mode: ``allow_all`` (default, no restrictions) or ``deny_all``
            (only whitelisted domains reachable).
        allowed_domains: Domain patterns allowed when mode is ``deny_all``.
            Supports wildcards (e.g., ``*.example.com``).
        denied_domains: Domains blocked even when they match an allowed
            pattern. Takes precedence over ``allowed_domains``.
    """

    mode: NetworkPolicyMode = NetworkPolicyMode.ALLOW_ALL
    allowed_domains: list[str] = field(default_factory=list)
    denied_domains: list[str] = field(default_factory=list)

    def is_domain_allowed(self, domain: str) -> bool:
        """Check if a domain is reachable under this policy.

        Args:
            domain: The destination domain to check.

        Returns:
            True if the domain is allowed, False if blocked.
        """
        if self.mode == NetworkPolicyMode.ALLOW_ALL:
            # In allow_all mode, check only explicit denies
            return not self._matches_any(domain, self.denied_domains)

        # In deny_all mode, require explicit allow AND not denied
        if self._matches_any(domain, self.denied_domains):
            return False
        return self._matches_any(domain, self.allowed_domains)

    def _matches_any(self, domain: str, patterns: list[str]) -> bool:
        """Check if domain matches any pattern (supports wildcard prefix).

        Args:
            domain: The domain to check (lowercased).
            patterns: List of patterns, may include ``*.example.com`` wildcards.

        Returns:
            True if any pattern matches.
        """
        domain_lower = domain.lower()
        for pattern in patterns:
            pattern_lower = pattern.lower()
            if pattern_lower.startswith("*."):
                suffix = pattern_lower[1:]  # ".example.com"
                if domain_lower.endswith(suffix) or domain_lower == pattern_lower[2:]:
                    return True
            elif domain_lower == pattern_lower:
                return True
        return False
