"""Tests for NetworkEgressPolicy (services/environment/network_policy.py)."""

from __future__ import annotations

import asyncio
import socket
from unittest.mock import AsyncMock, MagicMock

import pytest

from hecate.services.environment.network_policy import (
    NetworkEgressPolicy,
    NetworkPolicyMode,
    is_url_allowed,
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


class TestIsUrlAllowed:
    def test_empty_allowlist_denies_everything(self):
        assert is_url_allowed("https://example.com", []) is False

    def test_exact_host_match(self):
        assert is_url_allowed("https://example.com/path", ["example.com"]) is True

    def test_wildcard_subdomain_match(self):
        assert is_url_allowed("https://api.example.com/x", ["*.example.com"]) is True

    def test_wildcard_matches_apex(self):
        assert is_url_allowed("https://example.com", ["*.example.com"]) is True

    def test_wildcard_does_not_match_other_tld(self):
        assert is_url_allowed("https://example.org", ["*.example.com"]) is False

    def test_case_insensitive(self):
        assert is_url_allowed("https://Example.COM", ["example.com"]) is True

    def test_subdomain_does_not_match_apex_allowlist(self):
        assert is_url_allowed("https://api.example.com", ["example.com"]) is False

    def test_malformed_url_denied(self):
        assert is_url_allowed("not a url", ["example.com"]) is False

    def test_url_without_host_denied(self):
        assert is_url_allowed("https://", ["example.com"]) is False


@pytest.mark.asyncio
async def test_apply_to_container_allow_all_skips_iptables(monkeypatch):
    policy = NetworkEgressPolicy(mode=NetworkPolicyMode.ALLOW_ALL)
    fake_exec = AsyncMock()
    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    ok = await policy.apply_to_container("cid-abc")
    assert ok is True
    fake_exec.assert_not_called()


@pytest.mark.asyncio
async def test_apply_to_container_emits_iptables_rules(monkeypatch):
    """Verify the assembled docker exec invocation contains the right iptables commands."""
    policy = NetworkEgressPolicy(
        mode=NetworkPolicyMode.DENY_ALL,
        allowed_domains=["example.com"],
    )

    fake_proc = MagicMock()
    fake_proc.communicate = AsyncMock(return_value=(b"", b""))
    fake_proc.returncode = 0

    captured: dict[str, list[str]] = {}

    async def _fake_exec(*cmd, **_kwargs):
        captured["cmd"] = [str(c) for c in cmd]
        return fake_proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec)

    async def _fake_getaddrinfo(_host, _port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", _fake_getaddrinfo)

    ok = await policy.apply_to_container("cid-abc")
    assert ok is True
    assert "cmd" in captured
    cmd_str = " ".join(captured["cmd"])
    assert "docker exec cid-abc" in cmd_str
    assert "iptables" in cmd_str
    assert "93.184.216.34" in cmd_str
    assert "ACCEPT" in cmd_str


@pytest.mark.asyncio
async def test_apply_to_container_returns_false_on_iptables_failure(monkeypatch):
    policy = NetworkEgressPolicy(
        mode=NetworkPolicyMode.DENY_ALL,
        allowed_domains=["example.com"],
    )

    fake_proc = MagicMock()
    fake_proc.communicate = AsyncMock(return_value=(b"", b"Permission denied (you must be root)"))
    fake_proc.returncode = 1

    async def _fake_exec(*cmd, **_kwargs):
        return fake_proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec)

    async def _fake_getaddrinfo(_host, _port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", _fake_getaddrinfo)

    ok = await policy.apply_to_container("cid-abc")
    assert ok is False
