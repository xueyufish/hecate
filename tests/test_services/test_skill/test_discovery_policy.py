"""Unit tests for the 5.9c discovery policy helpers."""

from hecate.tools.skill.discovery import (
    DiscoveryPolicy,
    parse_workspace_policy,
    resolve_agent_policy,
)


class TestParseWorkspacePolicy:
    def test_missing_key_defaults_to_disabled(self):
        policy = parse_workspace_policy({"other": 1})
        assert policy == DiscoveryPolicy(enabled=False, min_trust_tier="community")

    def test_none_settings_defaults_to_disabled(self):
        assert parse_workspace_policy(None).enabled is False

    def test_non_dict_payload_fails_closed(self):
        assert parse_workspace_policy({"skill_discovery": "yes"}).enabled is False

    def test_enabled_true(self):
        policy = parse_workspace_policy({"skill_discovery": {"enabled": True}})
        assert policy.enabled is True
        assert policy.min_trust_tier == "community"

    def test_truthy_non_bool_fails_closed(self):
        assert parse_workspace_policy({"skill_discovery": {"enabled": 1}}).enabled is False

    def test_custom_trust_floor(self):
        policy = parse_workspace_policy({"skill_discovery": {"enabled": True, "min_trust_tier": "trusted"}})
        assert policy.min_trust_tier == "trusted"

    def test_unknown_tier_falls_back_with_default(self):
        policy = parse_workspace_policy({"skill_discovery": {"enabled": True, "min_trust_tier": "gold"}})
        assert policy.min_trust_tier == "community"


class TestDiscoveryPolicyAdmits:
    def test_default_floor_admits_community(self):
        assert DiscoveryPolicy().admits("community") is True

    def test_trusted_floor_rejects_community(self):
        policy = DiscoveryPolicy(enabled=True, min_trust_tier="trusted")
        assert policy.admits("community") is False
        assert policy.admits("trusted") is True
        assert policy.admits("official") is True

    def test_missing_tier_treated_as_community(self):
        assert DiscoveryPolicy().admits(None) is True
        assert DiscoveryPolicy(min_trust_tier="trusted").admits(None) is False


class TestResolveAgentPolicy:
    WORKSPACE_ON = DiscoveryPolicy(enabled=True)
    WORKSPACE_OFF = DiscoveryPolicy(enabled=False)

    def test_global_off_short_circuits_everything(self):
        policy = resolve_agent_policy(
            global_enabled=False,
            workspace_policy=self.WORKSPACE_ON,
            agent_override=True,
        )
        assert policy.enabled is False

    def test_agent_none_follows_workspace(self):
        assert (
            resolve_agent_policy(
                global_enabled=True,
                workspace_policy=self.WORKSPACE_ON,
                agent_override=None,
            ).enabled
            is True
        )
        assert (
            resolve_agent_policy(
                global_enabled=True,
                workspace_policy=self.WORKSPACE_OFF,
                agent_override=None,
            ).enabled
            is False
        )

    def test_agent_false_opts_out_of_enabled_workspace(self):
        assert (
            resolve_agent_policy(
                global_enabled=True,
                workspace_policy=self.WORKSPACE_ON,
                agent_override=False,
            ).enabled
            is False
        )

    def test_agent_true_cannot_exceed_disabled_workspace(self):
        assert (
            resolve_agent_policy(
                global_enabled=True,
                workspace_policy=self.WORKSPACE_OFF,
                agent_override=True,
            ).enabled
            is False
        )

    def test_trust_floor_survives_global_off(self):
        policy = resolve_agent_policy(
            global_enabled=False,
            workspace_policy=DiscoveryPolicy(enabled=True, min_trust_tier="trusted"),
            agent_override=None,
        )
        assert policy.min_trust_tier == "trusted"
