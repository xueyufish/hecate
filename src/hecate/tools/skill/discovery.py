"""Skill auto-detection policy resolution (5.9c).

Pure, DB-free helpers that decide whether skill discovery is active for an
agent and whether a skill's trust tier admits it into the discovery pool.
The discovery layers only narrow, never widen:

- global ``settings.SKILL_DISCOVERY_ENABLED`` (default off),
- workspace policy (``WorkspaceModel.settings`` JSON key
  ``skill_discovery``: ``{"enabled": bool, "min_trust_tier": str}``),
- per-agent three-state override (``AgentModel.skill_discovery_enabled``:
  ``None`` follows the workspace, ``True`` opts in, ``False`` opts out).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

WORKSPACE_SETTINGS_KEY = "skill_discovery"

DEFAULT_MIN_TRUST_TIER = "community"

# Higher ordinal = more trusted. Skills strictly below the configured floor
# are excluded from the discovery pool (explicit binding is unaffected).
TRUST_TIER_ORDER: dict[str, int] = {
    "community": 0,
    "trusted": 1,
    "official": 2,
}


@dataclass(frozen=True)
class DiscoveryPolicy:
    """Resolved discovery policy for one agent invocation."""

    enabled: bool = False
    min_trust_tier: str = DEFAULT_MIN_TRUST_TIER

    def admits(self, trust_tier: str | None) -> bool:
        """Return True when the tier meets or exceeds the configured floor.

        Unknown or missing tiers are treated as ``community``.
        """
        tier = TRUST_TIER_ORDER.get(trust_tier or DEFAULT_MIN_TRUST_TIER, 0)
        floor = TRUST_TIER_ORDER.get(self.min_trust_tier, 0)
        return tier >= floor


def parse_workspace_policy(settings: dict | None) -> DiscoveryPolicy:
    """Parse the ``skill_discovery`` key from workspace settings JSON.

    Unknown values fail closed: a non-dict payload or a disabled/absent
    ``enabled`` key yields ``enabled=False``; an unrecognised
    ``min_trust_tier`` falls back to the default with a warning.
    """
    raw = (settings or {}).get(WORKSPACE_SETTINGS_KEY)
    if not isinstance(raw, dict):
        return DiscoveryPolicy()

    # Strict True — anything else (strings, 1, None) fails closed.
    enabled = raw.get("enabled") is True

    tier = raw.get("min_trust_tier", DEFAULT_MIN_TRUST_TIER)
    if tier not in TRUST_TIER_ORDER:
        logger.warning(
            "Ignoring unknown skill_discovery.min_trust_tier %r; using %r",
            tier,
            DEFAULT_MIN_TRUST_TIER,
        )
        tier = DEFAULT_MIN_TRUST_TIER
    return DiscoveryPolicy(enabled=enabled, min_trust_tier=tier)


def resolve_agent_policy(
    *,
    global_enabled: bool,
    workspace_policy: DiscoveryPolicy,
    agent_override: bool | None,
) -> DiscoveryPolicy:
    """Combine the three governance layers for one agent.

    The global switch short-circuits everything. With it on, the workspace
    decides, and the agent override may only opt out (``False``) — an
    explicit ``True`` cannot exceed the workspace policy.
    """
    if not global_enabled:
        return DiscoveryPolicy(enabled=False, min_trust_tier=workspace_policy.min_trust_tier)
    enabled = workspace_policy.enabled and agent_override is not False
    return DiscoveryPolicy(enabled=enabled, min_trust_tier=workspace_policy.min_trust_tier)
