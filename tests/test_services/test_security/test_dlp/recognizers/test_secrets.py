"""Tests for SecretsRecognizer.

detect-secrets is an optional dependency in the ``[security]`` extra.
The test environment does NOT have it installed, so we inject a fake
``detect_secrets`` module tree via ``sys.modules`` patching and flip
the module-level ``_HAS_DETECT_SECRETS`` flag.

The fake modules use real :class:`types.ModuleType` instances (not
:class:`unittest.mock.MagicMock`) because Python's import system
inspects ``__path__`` and other module attributes — a MagicMock does
not satisfy these checks and causes ``ModuleNotFoundError`` during
the lazy ``from detect_secrets.core.plugins.core import …`` that
:func:`analyze` performs.
"""

from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import MagicMock

import pytest

import hecate.ops.dlp.recognizers.secrets as secrets_mod
from hecate.ops.dlp.recognizers.secrets import (
    _HAS_DETECT_SECRETS,
    SecretsRecognizer,
)


class AWSKeyDetector:
    pass


class JwtTokenDetector:
    pass


class PrivateKeyDetector:
    pass


class GitHubTokenDetector:
    pass


class DetectSecretsFake:
    """Mutable in-memory fake of the detect_secrets module tree.

    Exposes ``results_by_class`` so tests can configure per-plugin
    findings, ``called`` to inspect which plugins were invoked, and
    ``raise_for(plugin_name)`` to make a specific plugin raise (so we
    can exercise the resilience path in :func:`analyze`).
    """

    def __init__(self) -> None:
        self.results_by_class: dict[str, list[Any]] = {
            "AWSKeyDetector": [],
            "JwtTokenDetector": [],
            "PrivateKeyDetector": [],
            "GitHubTokenDetector": [],
        }
        self.called: list[str] = []
        self._impl = self._default_initialize

    def initialize(self, plugin_cls: Any) -> MagicMock:
        self.called.append(plugin_cls.__name__)
        return self._impl(plugin_cls)

    def _default_initialize(self, plugin_cls: Any) -> MagicMock:
        name = plugin_cls.__name__
        plugin = MagicMock()
        plugin.analyze_string = MagicMock(return_value=self.results_by_class[name])
        return plugin

    def raise_for(self, plugin_name: str, exc: Exception | None = None) -> None:
        exc = exc or RuntimeError("plugin blew up")

        def selective(plugin_cls: Any) -> MagicMock:
            if plugin_cls.__name__ == plugin_name:
                raise exc
            return self._default_initialize(plugin_cls)

        self._impl = selective


@pytest.fixture
def fake_detect_secrets(monkeypatch: pytest.MonkeyPatch) -> DetectSecretsFake:
    """Inject a fake ``detect_secrets`` module tree."""

    fake = DetectSecretsFake()

    fake_initialized = types.ModuleType("detect_secrets.core.plugins.initialized")
    fake_initialized.initialize = fake.initialize

    fake_core_plugins = types.ModuleType("detect_secrets.core.plugins")

    fake_core_plugins_core = types.ModuleType("detect_secrets.core.plugins.core")
    fake_core_plugins_core.AWSKeyDetector = AWSKeyDetector
    fake_core_plugins_core.JwtTokenDetector = JwtTokenDetector
    fake_core_plugins_core.PrivateKeyDetector = PrivateKeyDetector
    fake_core_plugins_core.GitHubTokenDetector = GitHubTokenDetector
    fake_core_plugins.core = fake_core_plugins_core

    fake_core = types.ModuleType("detect_secrets.core")
    fake_core.plugins = fake_core_plugins

    fake_root = types.ModuleType("detect_secrets")
    fake_root.core = fake_core

    monkeypatch.setitem(sys.modules, "detect_secrets", fake_root)
    monkeypatch.setitem(sys.modules, "detect_secrets.core", fake_core)
    monkeypatch.setitem(sys.modules, "detect_secrets.core.plugins", fake_core_plugins)
    monkeypatch.setitem(sys.modules, "detect_secrets.core.plugins.core", fake_core_plugins_core)
    monkeypatch.setitem(sys.modules, "detect_secrets.core.plugins.initialized", fake_initialized)

    return fake


@pytest.fixture
def enable_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flip the module-level flag so __init__ does not raise ImportError."""
    monkeypatch.setattr(secrets_mod, "_HAS_DETECT_SECRETS", True)


class TestSecretsRecognizerMetadata:
    def test_name(self) -> None:
        assert SecretsRecognizer.name == "secrets"

    def test_supported_entities(self) -> None:
        assert set(SecretsRecognizer.supported_entities) == {
            "AWS_ACCESS_KEY",
            "GITHUB_TOKEN",
            "JWT_TOKEN",
            "PRIVATE_KEY",
        }


class TestSecretsRecognizerInit:
    def test_init_without_detect_secrets_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(secrets_mod, "_HAS_DETECT_SECRETS", False)
        with pytest.raises(ImportError, match="detect-secrets"):
            SecretsRecognizer()

    def test_init_with_detect_secrets_succeeds(
        self, enable_secrets: None, fake_detect_secrets: types.ModuleType
    ) -> None:
        rec = SecretsRecognizer()
        assert rec is not None


class TestSecretsRecognizerAnalyze:
    def test_analyze_empty_text_returns_empty(
        self, enable_secrets: None, fake_detect_secrets: types.ModuleType
    ) -> None:
        rec = SecretsRecognizer()
        assert rec.analyze("") == []

    def test_analyze_no_secrets_returns_empty(
        self, enable_secrets: None, fake_detect_secrets: types.ModuleType
    ) -> None:
        rec = SecretsRecognizer()
        assert rec.analyze("clean text with no secrets") == []

    def test_analyze_aws_key_detected(self, enable_secrets: None, fake_detect_secrets: types.ModuleType) -> None:
        aws_value = "AKIAIOSFODNN7EXAMPLE"
        fake_detect_secrets.results_by_class["AWSKeyDetector"] = [MagicMock(secret_value=aws_value)]
        rec = SecretsRecognizer()
        text = f"my key is {aws_value} here"
        findings = rec.analyze(text)
        aws_findings = [f for f in findings if f.entity_type == "AWS_ACCESS_KEY"]
        assert len(aws_findings) == 1
        assert aws_findings[0].value == aws_value
        assert aws_findings[0].start == text.find(aws_value)
        assert aws_findings[0].end == text.find(aws_value) + len(aws_value)
        assert aws_findings[0].score == 1.0
        assert aws_findings[0].recognizer == "secrets"

    def test_analyze_jwt_token_detected(self, enable_secrets: None, fake_detect_secrets: types.ModuleType) -> None:
        jwt_value = "eyJhbGciOiJIUzI1NiJ9.payload.signature"
        fake_detect_secrets.results_by_class["JwtTokenDetector"] = [MagicMock(secret_value=jwt_value)]
        rec = SecretsRecognizer()
        findings = rec.analyze(f"token: {jwt_value}")
        jwt_findings = [f for f in findings if f.entity_type == "JWT_TOKEN"]
        assert len(jwt_findings) == 1
        assert jwt_findings[0].value == jwt_value

    def test_analyze_github_token_detected(self, enable_secrets: None, fake_detect_secrets: types.ModuleType) -> None:
        gh_value = "ghp_1234567890abcdefghij"
        fake_detect_secrets.results_by_class["GitHubTokenDetector"] = [MagicMock(secret_value=gh_value)]
        rec = SecretsRecognizer()
        findings = rec.analyze(f"see {gh_value}")
        gh_findings = [f for f in findings if f.entity_type == "GITHUB_TOKEN"]
        assert len(gh_findings) == 1

    def test_analyze_filters_by_entity_types(
        self, enable_secrets: None, fake_detect_secrets: DetectSecretsFake
    ) -> None:
        fake_detect_secrets.results_by_class["AWSKeyDetector"] = [MagicMock(secret_value="AKIA_AWS")]  # noqa: S106
        fake_detect_secrets.results_by_class["JwtTokenDetector"] = [MagicMock(secret_value="eyJ.x.y")]  # noqa: S106
        rec = SecretsRecognizer()
        findings = rec.analyze("text", entities=["AWS_ACCESS_KEY"])
        assert "AWSKeyDetector" in fake_detect_secrets.called
        assert "JwtTokenDetector" not in fake_detect_secrets.called
        assert all(f.entity_type == "AWS_ACCESS_KEY" for f in findings)

    def test_analyze_skips_empty_secret_values(
        self, enable_secrets: None, fake_detect_secrets: types.ModuleType
    ) -> None:
        fake_detect_secrets.results_by_class["AWSKeyDetector"] = [
            MagicMock(secret_value=""),
            MagicMock(secret_value=None),
            MagicMock(secret_value="AKIA_REAL"),  # noqa: S106
        ]
        rec = SecretsRecognizer()
        findings = rec.analyze("AKIA_REAL is the only valid")
        assert len(findings) == 1
        assert findings[0].value == "AKIA_REAL"

    def test_analyze_skips_value_not_in_text(self, enable_secrets: None, fake_detect_secrets: types.ModuleType) -> None:
        fake_detect_secrets.results_by_class["AWSKeyDetector"] = [MagicMock(secret_value="AKIA_NOT_PRESENT")]  # noqa: S106
        rec = SecretsRecognizer()
        assert rec.analyze("clean text without that secret") == []

    def test_analyze_continues_when_plugin_raises(
        self, enable_secrets: None, fake_detect_secrets: DetectSecretsFake
    ) -> None:
        fake_detect_secrets.raise_for("AWSKeyDetector")
        fake_detect_secrets.results_by_class["JwtTokenDetector"] = [MagicMock(secret_value="eyJ.x.y")]  # noqa: S106
        rec = SecretsRecognizer()
        findings = rec.analyze("eyJ.x.y here")
        jwt_findings = [f for f in findings if f.entity_type == "JWT_TOKEN"]
        assert len(jwt_findings) == 1


class TestSecretsRecognizerDependencyDetection:
    def test_module_flag_reflects_actual_import(self) -> None:
        assert isinstance(_HAS_DETECT_SECRETS, bool)
