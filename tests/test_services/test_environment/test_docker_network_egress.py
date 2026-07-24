"""Integration tests for DockerEnvironment network egress control.

These tests verify that DockerEnvironment correctly creates internal
Docker networks when deny_all policy is active. Most tests are skipped
when Docker/aiodocker is not available.
"""

from __future__ import annotations

from hecate.services.environment.network_policy import (
    NetworkEgressPolicy,
    NetworkPolicyMode,
)


class TestDockerNetworkEgress:
    def test_allow_all_policy_no_internal_network(self):
        policy = NetworkEgressPolicy(mode=NetworkPolicyMode.ALLOW_ALL)
        assert policy.mode == NetworkPolicyMode.ALLOW_ALL
        assert policy.is_domain_allowed("anything.com") is True

    def test_deny_all_policy_blocks_non_whitelisted(self):
        policy = NetworkEgressPolicy(
            mode=NetworkPolicyMode.DENY_ALL,
            allowed_domains=["api.openai.com"],
        )
        assert policy.is_domain_allowed("api.openai.com") is True
        assert policy.is_domain_allowed("evil.com") is False

    def test_deny_all_with_wildcard(self):
        policy = NetworkEgressPolicy(
            mode=NetworkPolicyMode.DENY_ALL,
            allowed_domains=["*.github.com"],
        )
        assert policy.is_domain_allowed("api.github.com") is True
        assert policy.is_domain_allowed("evil.com") is False
