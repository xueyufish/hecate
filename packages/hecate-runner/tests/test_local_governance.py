"""Runner local policy profile (tasks 5.1-5.3, 5.5)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from hecate_governance.policy import GovernanceService, PolicyContext
from hecate_governance.principal import ActionPrincipal, PrincipalSource
from hecate_runner.governance import LocalPolicyProfile, LocalRevocationView
from hecate_runner.profile import ProfileError, load_profile


@pytest.fixture(autouse=True)
def _shutdown_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNNER_SHUTDOWN_TOKEN", "shutdown")


def _now() -> datetime:
    return datetime(2026, 10, 11, 12, 0, 0, tzinfo=UTC)


def _principal(actor: str = "app-writer", domains: tuple[str, ...] = ("domain_a",)) -> ActionPrincipal:
    return ActionPrincipal.from_verified_claims(
        {"sub": actor},
        source=PrincipalSource.RUNNER_LOCAL,
        domains=domains,
        deployment_id="dealer-app",
    )


def _context(actor: str = "app-writer", tool: str = "query_inventory", **kwargs) -> PolicyContext:
    base: dict = {
        "principal": _principal(actor),
        "tool_name": tool,
        "arguments": {},
        "arguments_digest": "d",
        "invocation_id": "tool-1",
        "deployment_id": "dealer-app",
    }
    base.update(kwargs)
    return PolicyContext(**base)


def write_governance_profile(tmp_path: Path, governance_block: dict | None, *, with_write_tool: bool = False) -> Path:
    """Materialize a profile with a ``governance`` block patched into
    runner.json (write_profile has no governance knob)."""
    import json

    from conftest_runner import write_profile

    profile_dir = write_profile(
        tmp_path,
        with_write_tool=with_write_tool,
        durable={"database_url": f"sqlite:///{tmp_path / 'state.db'}", "workspace": "ws"} if with_write_tool else None,
    )
    config_path = profile_dir / "runner.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if governance_block is not None:
        config["governance"] = governance_block
    config_path.write_text(json.dumps(config), encoding="utf-8")
    return profile_dir


CONFIG: dict = {
    "rules": [
        {"tool": "query_inventory", "domains": ["domain_a"]},
        {"tool": "submit_inventory_update", "domains": ["domain_a"], "approval_required": True},
    ],
    "approvers": ["approver-1"],
    "revoked_principals": ["bad-actor"],
}


class TestConfigParsing:
    def test_defaults_are_fail_closed(self, tmp_path) -> None:
        profile = load_profile(write_governance_profile(tmp_path, None, with_write_tool=True))
        assert profile.governance.rules == ()
        assert profile.governance.write_enablement == "disabled"
        assert profile.governance.approvers == ()

    def test_malformed_rules_fail_with_clear_error(self, tmp_path) -> None:
        with pytest.raises(ProfileError, match="governance.rules"):
            load_profile(write_governance_profile(tmp_path, {"rules": [{"domains": ["domain_a"]}], "approvers": ["a"]}))

    def test_experimental_writes_require_attestation(self, tmp_path) -> None:
        with pytest.raises(ProfileError, match="write_attestation"):
            load_profile(write_governance_profile(tmp_path, {"write_enablement": "experimental"}))

    def test_unknown_enablement_mode_rejected(self, tmp_path) -> None:
        with pytest.raises(ProfileError, match="write_enablement"):
            load_profile(write_governance_profile(tmp_path, {"write_enablement": "open"}))


class TestLocalAuthorizationLoop:
    async def test_allowlisted_tool_allowed(self, tmp_path) -> None:
        profile = load_profile(write_governance_profile(tmp_path, CONFIG, with_write_tool=True))
        service = GovernanceService(LocalPolicyProfile(profile.governance))
        decision = await service.authorize(_context(), now=_now())
        assert decision.allowed

    async def test_unlisted_tool_denied_by_default(self, tmp_path) -> None:
        profile = load_profile(write_governance_profile(tmp_path, CONFIG))
        service = GovernanceService(LocalPolicyProfile(profile.governance))
        decision = await service.authorize(_context(tool="unknown_tool"), now=_now())
        assert not decision.allowed
        assert "not locally authorized" in decision.reason

    async def test_domain_outside_grant_denied(self, tmp_path) -> None:
        profile = load_profile(write_governance_profile(tmp_path, CONFIG))
        service = GovernanceService(LocalPolicyProfile(profile.governance))
        outsider = PolicyContext(
            principal=_principal(actor="app-writer", domains=("domain_b",)),
            tool_name="query_inventory",
            arguments={},
            arguments_digest="d",
            deployment_id="dealer-app",
        )
        decision = await service.authorize(outsider, now=_now())
        assert not decision.allowed

    async def test_no_platform_iam_required(self, tmp_path) -> None:
        # The closed loop runs purely on the local config: no platform
        # service, no organization directory, no approval UI.
        profile = load_profile(write_governance_profile(tmp_path, CONFIG))
        service = GovernanceService(
            LocalPolicyProfile(profile.governance),
            revocation=LocalRevocationView(profile.governance),
        )
        decision = await service.authorize(_context(), now=_now())
        assert decision.allowed

    async def test_identity_adapter_hook_extends_context(self, tmp_path) -> None:
        # The business App may register an adapter that supplies additional
        # verified context; the local profile remains the sole policy
        # authority for dispatch decisions.
        profile = load_profile(write_governance_profile(tmp_path, CONFIG))

        class _Adapter:
            async def enrich(self, principal: ActionPrincipal) -> dict:
                return {"app_role": "inventory-clerk"}

        enriched = await _Adapter().enrich(_principal())
        assert enriched["app_role"] == "inventory-clerk"
        service = GovernanceService(LocalPolicyProfile(profile.governance))
        assert (await service.authorize(_context(), now=_now())).allowed


class TestLocalRevocation:
    async def test_revoked_principal_denied_immediately(self, tmp_path) -> None:
        profile = load_profile(write_governance_profile(tmp_path, CONFIG))
        service = GovernanceService(
            LocalPolicyProfile(profile.governance),
            revocation=LocalRevocationView(profile.governance),
        )
        decision = await service.authorize(_context(actor="bad-actor"), now=_now())
        assert not decision.allowed
        assert decision.violation.value == "principal_revoked"

    async def test_revocation_does_not_rewrite_history(self, tmp_path) -> None:
        # Revocation affects new dispatches only; existing execution facts
        # are untouched by construction.
        profile = load_profile(write_governance_profile(tmp_path, CONFIG))
        view = LocalRevocationView(profile.governance)
        assert await view.is_revoked(_principal(actor="bad-actor")) is True
        assert await view.is_revoked(_principal(actor="app-writer")) is False


class TestWriteEnablementGate:
    async def test_disabled_by_default_refuses_writes(self, tmp_path) -> None:
        profile = load_profile(write_governance_profile(tmp_path, None, with_write_tool=True))
        service = GovernanceService(LocalPolicyProfile(profile.governance))
        decision = await service.authorize(
            _context(tool="submit_inventory_update", tool_side_effect="non_idempotent_write"),
            now=_now(),
        )
        assert not decision.allowed
        assert "not enabled" in decision.reason

    async def test_experimental_tier_allows_declared_writes(self, tmp_path) -> None:
        config = {
            "rules": [{"tool": "submit_inventory_update", "domains": ["domain_a"]}],
            "write_enablement": "experimental",
            "write_attestation": "business API independently enforces final authorization",
        }
        profile = load_profile(write_governance_profile(tmp_path, config, with_write_tool=True))
        service = GovernanceService(LocalPolicyProfile(profile.governance))
        decision = await service.authorize(
            _context(tool="submit_inventory_update", tool_side_effect="non_idempotent_write"),
            now=_now(),
        )
        assert decision.allowed
        assert "experimental" in decision.reason

    async def test_readonly_dispatch_unaffected_by_write_gate(self, tmp_path) -> None:
        profile = load_profile(write_governance_profile(tmp_path, None, with_write_tool=True))
        service = GovernanceService(LocalPolicyProfile(profile.governance))
        decision = await service.authorize(_context(tool="query_inventory", tool_side_effect="readonly"), now=_now())
        assert not decision.allowed  # no rules configured → default deny
        assert "not enabled" not in decision.reason

    async def test_allowlisted_readonly_passes_with_gate_active(self, tmp_path) -> None:
        profile = load_profile(write_governance_profile(tmp_path, CONFIG, with_write_tool=True))
        service = GovernanceService(LocalPolicyProfile(profile.governance))
        decision = await service.authorize(_context(tool="query_inventory", tool_side_effect="readonly"), now=_now())
        assert decision.allowed
