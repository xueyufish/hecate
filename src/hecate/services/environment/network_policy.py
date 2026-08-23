"""Network egress policy for DockerEnvironment.

Controls outbound network access from agent containers. When mode is
``deny_all``, only whitelisted domains are reachable through an egress
proxy; all other traffic is blocked.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from dataclasses import dataclass, field
from enum import StrEnum
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


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
            return not self._matches_any(domain, self.denied_domains)

        if self._matches_any(domain, self.denied_domains):
            return False
        return self._matches_any(domain, self.allowed_domains)

    def _matches_any(self, domain: str, patterns: list[str]) -> bool:
        """Check if domain matches any pattern (supports wildcard prefix)."""
        domain_lower = domain.lower()
        for pattern in patterns:
            pattern_lower = pattern.lower()
            if pattern_lower.startswith("*."):
                suffix = pattern_lower[1:]
                if domain_lower.endswith(suffix) or domain_lower == pattern_lower[2:]:
                    return True
            elif domain_lower == pattern_lower:
                return True
        return False

    async def apply_to_container(self, container_id: str) -> bool:
        """Inject iptables rules inside a sandbox container.

        Resolves every allowed domain to its current A/AAAA records and emits
        ``iptables -A OUTPUT -d <ip> -j ACCEPT`` rules, finishing with a
        ``-A OUTPUT -j REJECT --reject-with icmp-net-unreachable`` default
        deny. Failures (typically ``Permission denied`` when the container
        was started without ``--cap-add=NET_ADMIN``) are logged and return
        ``False`` without raising so the calling code can decide whether to
        treat the lack of network-layer enforcement as a hard failure.

        Args:
            container_id: Docker container id or name.

        Returns:
            ``True`` when the iptables rules were installed, ``False`` when
            installation failed (typically because the container lacks the
            ``NET_ADMIN`` capability).
        """
        if self.mode == NetworkPolicyMode.ALLOW_ALL:
            return True

        rules: list[str] = []
        for pattern in self.allowed_domains:
            host = pattern[2:] if pattern.startswith("*.") else pattern
            try:
                infos = await asyncio.get_running_loop().getaddrinfo(host, None)
            except socket.gaierror:
                logger.warning("network_policy: cannot resolve %s, skipping rule", pattern)
                continue
            ips: set[str] = set()
            for info in infos:
                sockaddr = info[4]
                ips.add(str(sockaddr[0]))
            for ip in sorted(ips):
                rules.extend(_iptables_allow_rule(ip))

        cmd = ["docker", "exec", container_id, "sh", "-c", " && ".join(rules)] if rules else None
        if not cmd:
            return True

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
        except Exception as exc:
            logger.warning("network_policy: failed to invoke iptables in %s: %s", container_id[:12], exc)
            return False

        if proc.returncode != 0:
            logger.warning(
                "network_policy: iptables failed inside %s (likely missing NET_ADMIN): %s",
                container_id[:12],
                stderr.decode(errors="replace").strip(),
            )
            return False
        return True


def _iptables_allow_rule(ip: str) -> list[str]:
    """Emit an iptables -A OUTPUT -d <ip> -j ACCEPT command (and the default REJECT)."""
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv4Address):
        return [f"iptables -A OUTPUT -d {ip} -j ACCEPT"]
    return [f"ip6tables -A OUTPUT -d {ip} -j ACCEPT"]


def is_url_allowed(url: str, allowed_domains: list[str]) -> bool:
    """Pure-function URL allow-list check for browser tools.

    Parses ``url`` to extract its host, then checks it against
    ``allowed_domains`` using exact-match or wildcard-suffix-match
    (``*.example.com``). When ``allowed_domains`` is empty the URL is
    denied (fail-closed).

    Args:
        url: Absolute URL to evaluate.
        allowed_domains: Allowed host patterns (exact or ``*.`` prefix).

    Returns:
        ``True`` if the URL's host matches the allow-list, ``False`` otherwise
        (including malformed URLs and empty allow-lists).
    """
    if not allowed_domains:
        return False
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    for pattern in allowed_domains:
        pattern_lower = pattern.lower()
        if pattern_lower.startswith("*."):
            suffix = pattern_lower[1:]
            if host.endswith(suffix) or host == pattern_lower[2:]:
                return True
        elif host == pattern_lower:
            return True
    return False
