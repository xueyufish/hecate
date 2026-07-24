"""Integration tests for DockerEnvironment credential scoping.

These tests verify that CredentialScope correctly strips secrets
from the environment before tool execution. Docker-specific tests
are skipped when Docker/aiodocker is not available.
"""

from __future__ import annotations

from hecate.services.environment.credential_scope import CredentialScope


class TestDockerCredentialScoping:
    def test_scoping_enabled_strips_api_keys(self):
        scope = CredentialScope(enabled=True)
        env = {
            "PATH": "/usr/bin",
            "HOME": "/root",
            "OPENAI_API_KEY": "sk-xxx",
            "DATABASE_PASSWORD": "secret",
        }
        result = scope.sanitize_environment(env)
        assert "PATH" in result
        assert "HOME" in result
        assert "OPENAI_API_KEY" not in result
        assert "DATABASE_PASSWORD" not in result

    def test_scoping_disabled_preserves_all(self):
        scope = CredentialScope(enabled=False)
        env = {"PATH": "/usr/bin", "OPENAI_API_KEY": "sk-xxx"}
        result = scope.sanitize_environment(env)
        assert result == env

    def test_tool_credentials_injected_when_configured(self):
        scope = CredentialScope(
            enabled=True,
            tool_credentials={"salesforce": ["SALESFORCE_TOKEN"]},
        )
        env = {
            "PATH": "/usr/bin",
            "SALESFORCE_TOKEN": "tok123",
            "OPENAI_API_KEY": "sk-xxx",
        }
        result = scope.sanitize_environment(env, tool_name="salesforce")
        assert "SALESFORCE_TOKEN" in result
        assert "OPENAI_API_KEY" not in result

    def test_other_tool_cannot_access_unscoped_credentials(self):
        scope = CredentialScope(
            enabled=True,
            tool_credentials={"salesforce": ["SALESFORCE_TOKEN"]},
        )
        env = {"PATH": "/usr/bin", "SALESFORCE_TOKEN": "tok123"}
        result = scope.sanitize_environment(env, tool_name="other_tool")
        assert "SALESFORCE_TOKEN" not in result
