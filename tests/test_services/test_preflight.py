"""Tests for preflight checks — LLM credential detection."""

from __future__ import annotations

from hecate.services.preflight import LLM_CREDENTIAL_ENV_VARS, _check_llm_credentials


async def test_llm_credentials_pass_when_key_present(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    result = await _check_llm_credentials()

    assert result.passed
    assert result.level == "WARN"
    assert "OPENAI_API_KEY" in result.detail


async def test_llm_credentials_warn_when_no_key(monkeypatch):
    for var in LLM_CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)

    result = await _check_llm_credentials()

    assert not result.passed
    assert result.level == "WARN"
    assert "chat requests will fail" in result.detail
