"""Tests for MCP boundary authentication and authorization."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from hecate.core.config import settings
from hecate.enterprise.auth.api_key_service import ApiKeyService
from hecate.models.api_key import ApiKeyScope
from hecate.tools.gateway.authz import (
    CallerIdentity,
    GatewayAuthorizer,
    resolve_caller_identity,
)
from hecate.tools.policy import PolicyDecision, ToolPolicyPipeline


def _layers_stub(decision=None, exc=None):
    """Build an authorizer with a single controllable stub layer."""
    from hecate.tools.policy.policy_pipeline import PolicyContext, PolicyLayer, ToolInfo

    class StubLayer(PolicyLayer):
        @property
        def name(self) -> str:
            return "stub"

        def evaluate(self, tool: ToolInfo, context: PolicyContext) -> PolicyDecision:
            if exc is not None:
                raise exc
            return decision or PolicyDecision.ALLOW

    pipeline = ToolPolicyPipeline(layers=[StubLayer()])
    return GatewayAuthorizer(pipeline=pipeline)


class TestCallerIdentity:
    async def test_workspace_scoped_key_resolves_workspace(self, db_session, monkeypatch) -> None:
        monkeypatch.setattr(settings, "HECATE_API_KEYS", "")
        _, raw = await ApiKeyService().create_key(
            db_session,
            name="gw",
            scope=ApiKeyScope.WORKSPACE,
            created_by=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
        )
        identity = await resolve_caller_identity(db_session, {"x-api-key": raw})
        assert identity.scope == "workspace"
        assert identity.workspace_id is not None
        assert identity.authenticated is True

    async def test_system_scoped_key_sees_platform_federation(self, db_session, monkeypatch) -> None:
        monkeypatch.setattr(settings, "HECATE_API_KEYS", "")
        _, raw = await ApiKeyService().create_key(
            db_session,
            name="sys",
            scope=ApiKeyScope.SYSTEM,
            created_by=uuid.uuid4(),
        )
        identity = await resolve_caller_identity(db_session, {"x-api-key": raw})
        assert identity.scope == "system"
        assert identity.include_platform_federated is True

    async def test_expired_key_rejected(self, db_session, monkeypatch) -> None:
        monkeypatch.setattr(settings, "HECATE_API_KEYS", "")
        _, raw = await ApiKeyService().create_key(
            db_session,
            name="stale",
            scope=ApiKeyScope.WORKSPACE,
            created_by=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
        with pytest.raises(PermissionError):
            await resolve_caller_identity(db_session, {"x-api-key": raw})

    async def test_legacy_env_key_falls_back_to_platform(self, db_session, monkeypatch) -> None:
        monkeypatch.setattr(settings, "HECATE_API_KEYS", "legacy-key")
        identity = await resolve_caller_identity(db_session, {"x-api-key": "legacy-key"})
        assert identity.scope == "platform"
        assert identity.include_platform_federated is False

    async def test_invalid_key_rejected(self, db_session, monkeypatch) -> None:
        monkeypatch.setattr(settings, "HECATE_API_KEYS", "")
        with pytest.raises(PermissionError, match="Invalid"):
            await resolve_caller_identity(db_session, {"x-api-key": "bad-key"})

    async def test_missing_header_rejected(self, db_session, monkeypatch) -> None:
        monkeypatch.setattr(settings, "HECATE_API_KEYS", "")
        with pytest.raises(PermissionError, match="Missing"):
            await resolve_caller_identity(db_session, {})

    async def test_jwt_without_header_rejected(self, db_session, monkeypatch) -> None:
        monkeypatch.setattr(settings, "MCP_AUTH_TYPE", "jwt")
        with pytest.raises(PermissionError, match="Authorization"):
            await resolve_caller_identity(db_session, {})

    async def test_auth_none_is_platform(self, db_session, monkeypatch) -> None:
        monkeypatch.setattr(settings, "MCP_AUTH_TYPE", "none")
        identity = await resolve_caller_identity(db_session, {})
        assert identity.scope == "platform"
        assert identity.authenticated is True


class TestGatewayAuthorizer:
    def test_default_pipeline_allows_low_risk_builtin(self) -> None:
        authorizer = GatewayAuthorizer()
        identity = CallerIdentity(workspace_id=None, scope="platform", authenticated=True)
        allowed, reason = authorizer.authorize(
            {"name": "agent_list", "source": "builtin", "risk_level": "low"}, identity
        )
        assert allowed, reason

    def test_deny_layer_short_circuits(self) -> None:
        authorizer = _layers_stub(decision=PolicyDecision.DENY)
        identity = CallerIdentity(workspace_id=None, scope="platform", authenticated=True)
        allowed, reason = authorizer.authorize({"name": "x"}, identity)
        assert not allowed
        assert "denied by policy" in reason

    def test_require_approval_denies_on_gateway(self) -> None:
        authorizer = _layers_stub(decision=PolicyDecision.REQUIRE_APPROVAL)
        identity = CallerIdentity(workspace_id=None, scope="platform", authenticated=True)
        allowed, _ = authorizer.authorize({"name": "x"}, identity)
        assert not allowed

    def test_execute_sandbox_denies_on_gateway(self) -> None:
        authorizer = _layers_stub(decision=PolicyDecision.EXECUTE_SANDBOX)
        identity = CallerIdentity(workspace_id=None, scope="platform", authenticated=True)
        allowed, _ = authorizer.authorize({"name": "x"}, identity)
        assert not allowed

    def test_pipeline_exception_fails_closed(self) -> None:
        authorizer = _layers_stub(exc=RuntimeError("boom"))
        identity = CallerIdentity(workspace_id=None, scope="platform", authenticated=True)
        allowed, reason = authorizer.authorize({"name": "x"}, identity)
        assert not allowed
        assert reason == "policy evaluation failed"

    def test_filter_catalog_removes_denied(self) -> None:
        from hecate.tools.policy.policy_pipeline import PolicyContext, PolicyLayer, ToolInfo

        class DenyX(PolicyLayer):
            @property
            def name(self) -> str:
                return "deny-x"

            def evaluate(self, tool: ToolInfo, context: PolicyContext) -> PolicyDecision:
                return PolicyDecision.DENY if tool.name == "x" else PolicyDecision.PASSTHROUGH

        authorizer = GatewayAuthorizer(pipeline=ToolPolicyPipeline(layers=[DenyX()]))
        identity = CallerIdentity(workspace_id=None, scope="platform", authenticated=True)
        visible = authorizer.filter_catalog([{"name": "x"}, {"name": "y"}], identity)
        assert [t["name"] for t in visible] == ["y"]

    def test_filter_catalog_exception_fails_closed_to_empty(self) -> None:
        from hecate.tools.policy.policy_pipeline import PolicyContext, PolicyLayer, ToolInfo

        class Exploding(PolicyLayer):
            @property
            def name(self) -> str:
                return "explode"

            def evaluate(self, tool: ToolInfo, context: PolicyContext) -> PolicyDecision:
                raise RuntimeError("boom")

        authorizer = GatewayAuthorizer(pipeline=ToolPolicyPipeline(layers=[Exploding()]))
        identity = CallerIdentity(workspace_id=None, scope="platform", authenticated=True)
        assert authorizer.filter_catalog([{"name": "x"}], identity) == []
