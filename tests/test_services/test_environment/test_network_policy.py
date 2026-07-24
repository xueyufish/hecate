"""Tests for NetworkEgressPolicy (services/environment/network_policy.py)."""

from __future__ import annotations

from hecate.services.environment.network_policy import (
    NetworkEgressPolicy,
    NetworkPolicyMode,
)


class TestNetworkEgressPolicy:
    def test_default_mode_is_allow_all(self):
        policy = NetworkEgressPolicy()
        assert policy.mode == NetworkPolicyMode.ALLOW_ALL

    def test_allow_all_permits_everything(self):
        policy = NetworkEgressPolicy(mode=NetworkPolicyMode.ALLOW_ALL)
        assert policy.is_domain_allowed("evil.com") is True
        assert policy.is_domain_allowed("anywhere.org") is True

    def test_allow_all_respects_denied_domains(self):
        policy = NetworkEgressPolicy(
            mode=NetworkPolicyMode.ALLOW_ALL,
            denied_domains=["evil.com"],
        )
        assert policy.is_domain_allowed("evil.com") is False
        assert policy.is_domain_allowed("good.com") is True

    def test_deny_all_blocks_everything_by_default(self):
        policy = NetworkEgressPolicy(mode=NetworkPolicyMode.DENY_ALL)
        assert policy.is_domain_allowed("evil.com") is False
        assert policy.is_domain_allowed("good.com") is False

    def test_deny_all_allows_whitelisted(self):
        policy = NetworkEgressPolicy(
            mode=NetworkPolicyMode.DENY_ALL,
            allowed_domains=["api.openai.com", "pypi.org"],
        )
        assert policy.is_domain_allowed("api.openai.com") is True
        assert policy.is_domain_allowed("pypi.org") is True
        assert policy.is_domain_allowed("evil.com") is False

    def test_denied_overrides_allowed(self):
        policy = NetworkEgressPolicy(
            mode=NetworkPolicyMode.DENY_ALL,
            allowed_domains=["*.example.com"],
            denied_domains=["bad.example.com"],
        )
        assert policy.is_domain_allowed("api.example.com") is True
        assert policy.is_domain_allowed("bad.example.com") is False

    def test_wildcard_matching(self):
        policy = NetworkEgressPolicy(
            mode=NetworkPolicyMode.DENY_ALL,
            allowed_domains=["*.github.com"],
        )
        assert policy.is_domain_allowed("api.github.com") is True
        assert policy.is_domain_allowed("raw.github.com") is True
        assert policy.is_domain_allowed("github.com") is True
        assert policy.is_domain_allowed("evil.com") is False

    def test_case_insensitive_matching(self):
        policy = NetworkEgressPolicy(
            mode=NetworkPolicyMode.DENY_ALL,
            allowed_domains=["PyPI.org"],
        )
        assert policy.is_domain_allowed("pypi.org") is True
        assert policy.is_domain_allowed("Pypi.Org") is True
