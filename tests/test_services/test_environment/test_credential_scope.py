"""Tests for CredentialScope (services/environment/credential_scope.py)."""

from __future__ import annotations

from hecate.services.environment.credential_scope import (
    CredentialScope,
)


class TestCredentialScopePatternDetection:
    def test_api_key_stripped(self):
        scope = CredentialScope(enabled=True)
        assert scope.should_strip("OPENAI_API_KEY") is True

    def test_secret_stripped(self):
        scope = CredentialScope(enabled=True)
        assert scope.should_strip("CLIENT_SECRET") is True

    def test_token_stripped(self):
        scope = CredentialScope(enabled=True)
        assert scope.should_strip("AUTH_TOKEN") is True

    def test_password_stripped(self):
        scope = CredentialScope(enabled=True)
        assert scope.should_strip("DB_PASSWORD") is True

    def test_pwd_stripped(self):
        scope = CredentialScope(enabled=True)
        assert scope.should_strip("REDIS_PWD") is True

    def test_key_stripped(self):
        scope = CredentialScope(enabled=True)
        assert scope.should_strip("AWS_SECRET_KEY") is True

    def test_hecate_secret_prefix_stripped(self):
        scope = CredentialScope(enabled=True)
        assert scope.should_strip("HECATE_SECRET_MY_CRED") is True

    def test_custom_pattern_stripped(self):
        scope = CredentialScope(
            enabled=True,
            custom_patterns=["*_CONNECTION_STRING"],
        )
        assert scope.should_strip("REDIS_CONNECTION_STRING") is True


class TestCredentialScopeWhitelist:
    def test_path_preserved(self):
        scope = CredentialScope(enabled=True)
        assert scope.should_strip("PATH") is False

    def test_home_preserved(self):
        scope = CredentialScope(enabled=True)
        assert scope.should_strip("HOME") is False

    def test_lang_preserved(self):
        scope = CredentialScope(enabled=True)
        assert scope.should_strip("LANG") is False

    def test_lc_vars_preserved(self):
        scope = CredentialScope(enabled=True)
        assert scope.should_strip("LC_ALL") is False
        assert scope.should_strip("LC_CTYPE") is False

    def test_tmpdir_preserved(self):
        scope = CredentialScope(enabled=True)
        assert scope.should_strip("TMPDIR") is False

    def test_user_preserved(self):
        scope = CredentialScope(enabled=True)
        assert scope.should_strip("USER") is False

    def test_non_secret_var_not_stripped(self):
        scope = CredentialScope(enabled=True)
        assert scope.should_strip("MY_CONFIG_VALUE") is False


class TestCredentialScopeSanitize:
    def test_disabled_scope_returns_unchanged(self):
        scope = CredentialScope(enabled=False)
        env = {"PATH": "/usr/bin", "OPENAI_API_KEY": "sk-xxx"}
        result = scope.sanitize_environment(env)
        assert result == env

    def test_enabled_strips_secrets(self):
        scope = CredentialScope(enabled=True)
        env = {"PATH": "/usr/bin", "OPENAI_API_KEY": "sk-xxx", "HOME": "/root"}
        result = scope.sanitize_environment(env)
        assert "PATH" in result
        assert "HOME" in result
        assert "OPENAI_API_KEY" not in result

    def test_tool_credentials_injected(self):
        scope = CredentialScope(
            enabled=True,
            tool_credentials={"my_tool": ["MY_TOOL_TOKEN"]},
        )
        env = {
            "PATH": "/usr/bin",
            "MY_TOOL_TOKEN": "tok123",
            "OPENAI_API_KEY": "sk-xxx",
        }
        result = scope.sanitize_environment(env, tool_name="my_tool")
        assert "PATH" in result
        assert "MY_TOOL_TOKEN" in result
        assert "OPENAI_API_KEY" not in result

    def test_tool_without_scope_gets_no_secrets(self):
        scope = CredentialScope(
            enabled=True,
            tool_credentials={"other_tool": ["OTHER_TOKEN"]},
        )
        env = {"PATH": "/usr/bin", "OTHER_TOKEN": "tok"}
        result = scope.sanitize_environment(env, tool_name="my_tool")
        assert "OTHER_TOKEN" not in result

    def test_multiple_secrets_all_stripped(self):
        scope = CredentialScope(enabled=True)
        env = {
            "PATH": "/usr/bin",
            "OPENAI_API_KEY": "sk-xxx",
            "ANTHROPIC_API_KEY": "sk-ant-xxx",
            "DATABASE_PASSWORD": "pass123",
            "JWT_SECRET": "jwt-secret",
            "HOME": "/root",
        }
        result = scope.sanitize_environment(env)
        assert "PATH" in result
        assert "HOME" in result
        assert "OPENAI_API_KEY" not in result
        assert "ANTHROPIC_API_KEY" not in result
        assert "DATABASE_PASSWORD" not in result
        assert "JWT_SECRET" not in result
